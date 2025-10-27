# /handlers/jira_webhook.py
import logging
from fastapi import Request
from langchain_core.messages import HumanMessage

from config import fastapi_app, active_workflows  # Import from central config
from graph.agent import app_graph  # Import the compiled graph
from config import jira_client  # for reconstructing mapping when needed

@fastapi_app.post("/webhook")
async def jira_webhook(request: Request):
    """
    This endpoint listens for webhook events from Jira. When a relevant
    event occurs on a managed ticket, it injects a new message into the
    existing agent conversation to trigger the next steps.
    """
    data = await request.json()
    event_type = data.get("webhookEvent")
    issue = data.get("issue", {})
    issue_key = issue.get("key")
    logging.info(f"Jira webhook received | type={event_type} | issue={issue_key}")

    # Filter for only the events we care about (allow common variants)
    allowed = {"comment_created", "jira:issue_updated", "issue_commented"}
    if event_type not in allowed:
        logging.info(f"Ignoring event type {event_type}")
        return {"status": "ignored_event_type"}

    try:
        # IMPORTANT: Check if the ticket is one that our agent is actively managing.
        if issue_key not in active_workflows:
            logging.info(f"Ticket {issue_key} not in active_workflows; attempting to reconstruct from Jira description.")
            try:
                j_issue = jira_client.issue(issue_key)
                desc = (j_issue.fields.description or "")
                # Expect lines like:
                # Slack thread: <channel>#<thread_ts>
                channel = None
                thread_ts = None
                for line in desc.split('\n'):
                    if line.lower().startswith("slack thread:") and '#' in line:
                        try:
                            value = line.split(":", 1)[1].strip()
                            channel, thread_ts = value.split('#', 1)
                            channel = channel.strip()
                            thread_ts = thread_ts.strip()
                            break
                        except Exception:
                            pass
                if channel and thread_ts:
                    active_workflows[issue_key] = {
                        "slack_channel": channel,
                        "slack_thread_ts": thread_ts,
                    }
                    logging.info(f"Reconstructed workflow mapping for {issue_key}: channel={channel} thread_ts={thread_ts}")
                else:
                    logging.info(f"Could not reconstruct mapping from description for {issue_key}. Will proceed without Slack context.")
            except Exception as e:
                logging.warning(f"Failed to reconstruct mapping for {issue_key}: {e}")

        # Retrieve the original Slack thread_ts to identify the correct conversation memory.
        workflow_info = active_workflows.get(issue_key, {})
        thread_ts = workflow_info.get("slack_thread_ts")
        # Prefer the Slack-thread based memory; if unavailable, fall back to a Jira-ticket scoped memory.
        thread_id = f"slack-{thread_ts}" if thread_ts else f"jira-{issue_key}"
        config = {"configurable": {"thread_id": thread_id}}

        comment_body = data.get("comment", {}).get("body", "No comment body")
        commenter_email = data.get("comment", {}).get("author", {}).get("emailAddress", "unknown")
        
        # This prompt informs the agent about the new event and gives it
        # instructions on how to proceed.
        prompt = (
            f"An update occurred on Jira ticket {issue_key}. A comment was added by {commenter_email} with the content: '{comment_body}'.\n"
            "Your task is to analyze this update. Use the `check_approval_status` tool to see if the request is now fully approved. "
            "If it is, use the `grant_access_and_close_ticket` tool. "
            "Finally, use the `send_slack_message` tool to inform the original user in their Slack thread about the outcome."
        )

        # Invoke the graph to add this new message to the existing conversation's memory.
        # The orchestrator is defensive against pending tool calls and will route appropriately.
        app_graph.invoke({"messages": [HumanMessage(content=prompt)]}, config=config)
        return {"status": "ok_processed"}

    except Exception as e:
        logging.error(f"Error processing webhook: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}