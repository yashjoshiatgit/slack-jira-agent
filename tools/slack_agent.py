# /tools/communication.py
import logging
from prompts.prompt import SLACK_SYSTEM_PROMPT

from langchain_core.tools import tool
from langchain.agents import create_agent
from config import slack_client,llm 

@tool
def send_slack_message(channel: str, text: str, thread_ts: str) -> str:
    """
    Sends a message to a specific Slack channel and thread.
    Use this to communicate updates, confirmations, or errors to the user.
    
    Args:
        channel: The Slack channel ID or name (e.g., '#access-requests' or 'C01234567').
        text: The message content to send.
        thread_ts: The timestamp of the thread to reply in (e.g., '1234567890.123456').
    
    Returns:
        Status message.
    """
    try:
        slack_client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
        logging.info(f"✅ Sent Slack message to {channel} (thread: {thread_ts})")
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

