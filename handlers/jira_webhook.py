import logging
import os
from fastapi import Request
from langchain_core.messages import HumanMessage
from config import fastapi_app, active_workflows  
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


    try:
        logging.info(f"===========================================================================================")
        workflow_info = active_workflows.get(issue_key, {})
        if not workflow_info:
            try:
                j_issue = jira_client.issue(issue_key)
                desc = (j_issue.fields.description or "")
                channel = None
                thread_ts = None
                for line in desc.split('\n'):
                    if 'slack thread:' in line.lower() and '#' in line:
                        try:
                            value = line.split(":", 1)[1].strip()
                            channel, thread_ts = value.split('#', 1)
                            channel = channel.strip(); thread_ts = thread_ts.strip()
                            break
                        except Exception:
                            pass
                if channel and thread_ts:
                    workflow_info = {"slack_channel": channel, "slack_thread_ts": thread_ts}
                    active_workflows[issue_key] = workflow_info
                    logging.info(f"Reconstructed workflow mapping for {issue_key}: channel={channel} thread_ts={thread_ts}")
            except Exception as e:
                logging.warning(f"Failed to reconstruct mapping for {issue_key}: {e}")

        comment_body = data.get("comment", {}).get("body", "")
        commenter_email = data.get("comment", {}).get("author", {}).get("emailAddress", "unknown")

        prompt_content = (
            f"A Jira event '{event_type}' occurred for issue '{issue_key}'.\n"
            f"Comment from {commenter_email}: {comment_body}"
            f"Comment Is from this ticket ID : {issue_key}"
        )

        thread_ts = workflow_info.get("slack_thread_ts")
        thread_id = f"slack-{thread_ts}" if thread_ts else f"jira-{issue_key}"
        config = {"configurable": {"thread_id": thread_id}}

        workflow.invoke({"messages": [HumanMessage(content=prompt_content)]}, config=config)
        return {"status": "ok_dispatched"}

    except Exception as e:
        logging.error(f"Error processing webhook: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}