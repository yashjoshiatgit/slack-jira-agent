from typing import Annotated, Literal
import logging
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver
from config import llm
from tools.slack_agent import Slack_Agent
from tools.ticket_agent import Jira_Agent
from tools.email_agent import Email_Agent
from tools.approval_agent import Approval_Agent
from tools.aws_agent import AWS_Agent
from prompts.prompt import ORCHESTRATOR_PROMPT
import asyncio

@tool
def call_slack_agent(task: str, channel_id: str, thread_ts: str) -> str:
    """
    Use this tool to handle Slack communications, sending messages, 
    or checking channels. Input should be a clear task description.

    When you use this tool, you must mention channel ID in the task details. Channel ID will be extracted by the LLM agents
    and then will be used to send messages.

    Args:
        task: The clear task description.
        channel_id: The Slack Channel ID to send the message to.
        thread_ts: The thread timestamp to reply to.
    """
    print(f"--- [Supervisor] Calling Slack Agent with task: {task} ---")

    context_info = ""
    if channel_id:
        context_info += f"\nIMPORTANT: Use Channel ID: {channel_id}"
    if thread_ts:
        context_info += f"\nIMPORTANT: Use Thread TS: {thread_ts}"
    
    full_task = f"{task}\n{context_info}"

    try:
        response = Slack_Agent.invoke({"messages": [HumanMessage(content=full_task)]})
        return response.get("output", str(response))
    except Exception as e:
        if "content_filter" in str(e) or "ResponsibleAIPolicyViolation" in str(e):
            logging.error(f"Content filter error in slack agent: {e}")
            return "Unable to send message due to content policy restrictions. Please rephrase the message."
        else:
            logging.error(f"Error in slack agent: {e}")
            return f"Error sending message: {str(e)}"


# @tool
# def call_slack_agent(task: str, channel_id: str = None, thread_ts: str = None) -> str:
#     """
#     Use this tool to handle Slack communications, sending messages, 
#     or checking channels. 
    
#     Args:
#         task: The clear task description.
#         channel_id: The Slack Channel ID (e.g., C12345) to send the message to.
#         thread_ts: The thread timestamp to reply to.
#     """
#     print(f"--- [Supervisor] Calling Slack Agent with task: {task} ---")

#     # Inject the channel context into the task for the sub-agent
#     context_info = ""
#     if channel_id:
#         context_info += f"\nIMPORTANT: Use Channel ID: {channel_id}"
#     if thread_ts:
#         context_info += f"\nIMPORTANT: Use Thread TS: {thread_ts}"
    
#     full_task = f"{task}\n{context_info}"

#     try:
#         response = Slack_Agent.invoke({"messages": [HumanMessage(content=full_task)]})
#         return response.get("output", str(response))
#     except Exception as e:
#         if "content_filter" in str(e) or "ResponsibleAIPolicyViolation" in str(e):
#             logging.error(f"Content filter error in slack agent: {e}")
#             return "Unable to send message due to content policy restrictions. Please rephrase the message."
#         else:
#             logging.error(f"Error in slack agent: {e}")
#             return f"Error sending message: {str(e)}"

@tool
def call_ticket_agent(task: str) -> str:
    """
    Use this tool to handle Jira ticket operations, creating tickets,
    or updating status. Input should be a clear task description.
    """
    print(f"--- [Supervisor] Calling Ticket Agent with task: {task} ---")

    try:
        response = Jira_Agent.invoke({"messages": [HumanMessage(content=task)]})
        return response.get("output", str(response))
    except Exception as e:
        if "content_filter" in str(e) or "ResponsibleAIPolicyViolation" in str(e):
            logging.error(f"Content filter error in ticket agent: {e}")
            return "Unable to process ticket request due to content policy restrictions. Please rephrase the request."
        else:
            logging.error(f"Error in ticket agent: {e}")
            return f"Error processing ticket request: {str(e)}"
        
@tool
def call_approval_agent(task: str) -> str:
    """
    Use this tool if for check weather the request is approved by the predefined policy
    Input should be a clear task description.
    """
    print(f"--- [Supervisor] Calling Approval Agent with task: {task} ---")

    try:
        response = Approval_Agent.invoke({"messages": [HumanMessage(content=task)]})
        return response.get("output", str(response))
    except Exception as e:
        if "content_filter" in str(e) or "ResponsibleAIPolicyViolation" in str(e):
            logging.error(f"Content filter error in Approval agent: {e}")
            return "Unable to process Policy request due to content policy restrictions. Please rephrase the request."
        else:
            logging.error(f"Error in Approval agent: {e}")
            return f"Error processing Approval request: {str(e)}"

@tool
def call_email_agent(task: str) -> str:
    """
    Use this tool to handle Email Related operations, Sending Mail,
    or follow ups to Mail. Input should be a clear task description.
    """
    print(f"--- [Supervisor] Calling Email Agent with task: {task} ---")

    try:
        response = Email_Agent.invoke({"messages": [HumanMessage(content=task)]})
        return response.get("output", str(response))
    except Exception as e:
        if "content_filter" in str(e) or "ResponsibleAIPolicyViolation" in str(e):
            logging.error(f"Content filter error in Email agent: {e}")
            return "Unable to process Email request due to content policy restrictions. Please rephrase the request."
        else:
            logging.error(f"Error in Email agent: {e}")
            return f"Error processing Email request: {str(e)}"

async def _call_aws_agent_async(task: str) -> str:
    payload = {"messages": [HumanMessage(content=task)]}
    print(payload)
    if hasattr(AWS_Agent, "ainvoke"):
        raw = await AWS_Agent.ainvoke(payload)
        print(raw)
    else:
        raw = await AWS_Agent.invoke(payload)
        print(raw)
    return raw.get("output", str(raw)) if isinstance(raw, dict) else str(raw)


@tool
def call_aws_agent(task: str) -> str:
    """Call AWS Agent When You need to perform operations related to
    to AWS."""
    print(f"--- [Supervisor] Calling AWS Agent with task: {task} ---")
    try:
        coro = _call_aws_agent_async(task)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is None or not loop.is_running():
            return asyncio.run(coro)
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=60)
    except TimeoutError:
        logging.exception("Timed out waiting for AWS agent")
        return "Timed out waiting for AWS agent"
    except Exception as e:
        logging.exception("AWS agent error")
        if "content_filter" in str(e) or "ResponsibleAIPolicyViolation" in str(e):
            return "Unable to process due to content policy. Please rephrase."
        return f"Error processing AWS request: {e}"
    

workflow = create_agent(
    model=llm,
    system_prompt=ORCHESTRATOR_PROMPT,
    tools=[call_slack_agent, call_ticket_agent,call_email_agent, call_approval_agent,call_aws_agent],
    checkpointer=MemorySaver()
)
