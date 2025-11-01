# /tools/communication.py
import logging
from prompts.prompt import SLACK_SYSTEM_PROMPT

from langchain_core.tools import tool
from langchain.agents import create_agent
from config import slack_client,llm 
from config import active_workflows

@tool
def send_slack_message(channel: str, text: str, thread_ts: str) -> str:
    """
    Sends a message to a specific Slack channel and thread.
    Uses step-tracking memory to avoid duplicate messages for the same thread/event.
    
    Args:
        channel: The Slack channel ID or name (e.g., '#access-requests' or 'C01234567').
        text: The message content to send.
        thread_ts: The timestamp of the thread to reply in (e.g., '1234567890.123456').
    
    Returns:
        Status message.
    """
      # Import here to avoid circular import
    # Use thread_ts as the unique key for workflow memory
    workflow = None
    ticket_id = None
    for tid, wf in active_workflows.items():
        if wf.get('slack_thread_ts') == thread_ts:
            workflow = wf
            ticket_id = tid
            break
    if workflow is None:
        # If not found, create a new workflow memory for this thread
        workflow = {"slack_channel": channel, "slack_thread_ts": thread_ts, "steps_completed": set()}
        ticket_id = f"unknown-{thread_ts}"
        active_workflows[ticket_id] = workflow

    steps = workflow.setdefault('steps_completed', set())
    # Define message type for step-tracking
    if text.startswith("Access request acknowledged"):
        step_name = 'slack_ack'
    elif text.startswith("Ticket") and "created" in text:
        step_name = 'ticket_notified'
    elif text.startswith("🎉 Request") and "approved" in text:
        step_name = 'approval_notified'
    else:
        step_name = f'slack_msg_{hash(text)}'

    if step_name in steps:
        logging.info(f"Duplicate Slack message for step '{step_name}' in thread {thread_ts} - skipping.")
        return f"Message already sent for this step: {step_name}. Skipping duplicate."

    try:
        slack_client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
        steps.add(step_name)
        logging.info(f"✅ Sent Slack message to {channel} (thread: {thread_ts}) and marked step '{step_name}'")
        return "Message sent successfully."
    except Exception as e:
        logging.error(f"❌ Failed to send Slack message: {e}")
        return f"Failed to send message: {str(e)}"
    
tools = [send_slack_message]

Slack_Agent = create_agent(
    llm,
    tools,
    system_prompt=SLACK_SYSTEM_PROMPT
)

