import logging
from prompts.prompt import SLACK_SYSTEM_PROMPT

from langchain_core.tools import tool
from langchain.agents import create_agent
from config import slack_client,llm 

@tool
def send_slack_message(channel: str, text: str, thread_ts: str = None) -> str:
    """
    Sends a message to a specific Slack channel and optional thread.

    Args:
        channel: Actual The Slack channel ID.
        text: The message content to send.
        thread_ts: The timestamp of the thread to reply in. If None, sends a new message.

    Returns:
        Status message indicating success or failure.
    """
    print("+++++++++++++++++++++++++ SLACK TOOLS ++++++++++++++++++++++++++++")
    print(f"SEND_SLACK_MESSSAGE def send_slack_message({channel}: str, {text}: str, {thread_ts}: str = None) ")
    try:
        slack_client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
        logging.info(f"✅ Sent Slack message to {channel}" + (f" (thread: {thread_ts})" if thread_ts else ""))
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

