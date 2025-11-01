# /handlers/jira_webhook.py
import logging
import os
from fastapi import Request
from langchain_core.messages import HumanMessage

from config import fastapi_app, active_workflows  # Import from central config
from graph.agent import workflow  # Import the compiled graph
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

    # Filter for only comment events (approval workflow)
    # Ignore issue_created and issue_updated to avoid duplicate processing
    allowed = {"comment_created", "issue_commented"}
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

        comment_body = data.get("comment", {}).get("body", "")
        commenter_email = data.get("comment", {}).get("author", {}).get("emailAddress", "unknown")
        
        # Skip if no meaningful comment
        if not comment_body or comment_body.startswith("==============>>"):
            logging.info(f"Ignoring system comment or empty comment on {issue_key}")
            return {"status": "ignored_system_comment"}
        
        ticket_link = f"{os.getenv('JIRA_URL', 'https://your-jira.atlassian.net')}/browse/{issue_key}"
        
        # Get Slack info from workflow mapping
        slack_channel = workflow_info.get("slack_channel", "UNKNOWN")
        slack_thread = workflow_info.get("slack_thread_ts", "UNKNOWN")
        
        # This prompt informs the agent about the new event and gives it
        # instructions on how to proceed.
        prompt = (
            f"JIRA APPROVAL UPDATE for ticket {issue_key}.\n"
            f"Comment by {commenter_email}: '{comment_body}'\n"
            f"Ticket: {ticket_link}\n"
            f"Slack Channel: {slack_channel}\n"
            f"Slack Thread: {slack_thread}\n\n"
            f"Workflow Steps:\n"
            f"1. Use scan_ticket_for_approvals to check if ticket {issue_key} is fully approved\n"
            f"2. If fully approved:\n"
            f"   a. Use transition_issue_to_done to close ticket {issue_key}\n"
            f"   b. Use send_slack_message with channel='{slack_channel}' and thread_ts='{slack_thread}' to notify: '🎉 Your access request in {issue_key} has been approved and access granted!'\n"
            f"   c. Use post_final_confirmation to clean up the workflow\n"
            f"3. If partially approved: Use send_slack_message to inform about pending approvers\n\n"
            f"IMPORTANT: Execute in order. Do not skip the Slack notification."
        )

        # Invoke the graph to add this new message to the existing conversation's memory.
        # The orchestrator is defensive against pending tool calls and will route appropriately.
        workflow.invoke({"messages": [HumanMessage(content=prompt)]}, config=config)
        return {"status": "ok_processed"}

    except Exception as e:
        logging.error(f"Error processing webhook: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}