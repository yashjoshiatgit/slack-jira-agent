import logging
import time
import json
from typing import TypedDict, List, Annotated, Literal, Optional

from langchain_core.messages import BaseMessage, SystemMessage, AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from config import llm  # Import the LLM from central config
from tools.communication import send_slack_message
from tools.jira_tools import (
    create_jira_ticket,
    find_approvers_and_notify,
    check_approval_status,
    grant_access_and_close_ticket
)

class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], lambda x, y: x + y]
    next: Optional[str]
    ticket_id: Optional[str]  # Track the Jira ticket ID
    approver: Optional[str]   # The specific person whose approval is needed
    approval_status: Optional[str]  # 'pending', 'approved', etc.
    ticket_status: Optional[str]  # 'open', 'closed'
    slack_thread_ts: Optional[str]  # Track Slack thread for notifications
    slack_notification_sent: Optional[bool]  # Whether notification was sent
    last_check_time: Optional[float]  # Timestamp of last approval check


slack_tools = [send_slack_message]
jira_tools = [
    create_jira_ticket,
    find_approvers_and_notify,
    check_approval_status,
    grant_access_and_close_ticket
]

slack_tool_node = ToolNode(slack_tools)
jira_tool_node = ToolNode(jira_tools)

slack_llm = llm.bind_tools(slack_tools)
jira_llm = llm.bind_tools(jira_tools)

def orchestrator_agent(state: AgentState):
    """The central orchestrator that decides the next action."""
    logging.info("ORCHESTRATOR: Deciding next action...")
    current_time = time.time()
    updates = {}
    # If there is a pending tool call in the message history (e.g., due to an external webhook inject),
    # route to the corresponding agent so the tool node can run before any new LLM calls.
    try:
        last = state['messages'][-1]
        if getattr(last, 'tool_calls', None):
            tool_name = last.tool_calls[0].get('name') if last.tool_calls else None
            jira_names = {t.name if hasattr(t, 'name') else t.__name__ for t in jira_tools}
            slack_names = {t.name if hasattr(t, 'name') else t.__name__ for t in slack_tools}
            if tool_name in jira_names:
                logging.info("ORCHESTRATOR: Detected pending Jira tool call; routing to jira agent.")
                return {"next": "jira", "messages": [SystemMessage(content="Process pending Jira tool call")]}
            if tool_name in slack_names:
                logging.info("ORCHESTRATOR: Detected pending Slack tool call; routing to slack agent.")
                return {"next": "slack", "messages": [SystemMessage(content="Process pending Slack tool call")]}
    except Exception:
        pass
    if state.get('approval_status') == 'pending':
        # If the latest message is a Jira webhook update, bypass the time gate and check now
        try:
            last_msg = state['messages'][-1]
            if isinstance(last_msg, HumanMessage) and isinstance(last_msg.content, str) and "An update occurred on Jira ticket" in last_msg.content:
                logging.info("ORCHESTRATOR: Received Jira update via webhook; routing to approval agent immediately.")
                updates['next'] = 'approval'
                updates['last_check_time'] = current_time
                return updates
        except Exception:
            pass
        if current_time - state.get('last_check_time', 0) >= 300:
            logging.info("ORCHESTRATOR: Time to check approval, routing to approval agent.")
            updates['next'] = 'approval'
            updates['last_check_time'] = current_time
            response = SystemMessage(content="Time to check approval status.")
            updates['messages'] = [response]
        else:
            logging.info("ORCHESTRATOR: Not time to check yet, finishing.")
            updates['next'] = 'finish'
    else:
        # Deterministic short-circuits to avoid infinite loops
        if state.get('approval_status') == 'approved' and state.get('ticket_status') != 'closed':
            logging.info("ORCHESTRATOR: Detected approved but not closed -> routing to jira to close.")
            updates = {"next": "jira"}
        elif state.get('ticket_status') == 'closed' and not state.get('slack_notification_sent', False):
            logging.info("ORCHESTRATOR: Ticket closed and no Slack notify -> routing to slack.")
            updates = {"next": "slack"}
        else:
            system_prompt = """
You are an orchestrator agent managing a workflow involving Slack, Jira, and a dedicated Approval agent.
Analyze the current conversation and state:
- If approval_status == 'approved' and ticket_status != 'closed', choose 'jira' to grant access and close.
- If ticket_status == 'closed' and slack_notification_sent == False, choose 'slack' to send notification.
- If a new Slack message is received related to the ticket (check messages), process it accordingly.
- If the task requires sending messages or communication via Slack, choose 'slack'.
- If the task requires creating, updating, checking, or closing Jira tickets, or handling approvals, choose 'jira'.
- If the task specifically involves computing approvers or checking approval progress for a requester, choose 'approval'.
- If the workflow is complete (e.g., ticket_status == 'closed' and slack_notification_sent == True), choose 'finish'.

Output ONLY the choice: slack, jira, approval, or finish. Do not add any other text.
"""
            messages = [SystemMessage(content=system_prompt)] + state['messages']
            response = llm.invoke(messages)
            choice = response.content.strip().lower()
            logging.info(f"ORCHESTRATOR: Choice: {choice}")
            updates = {"messages": [response], "next": choice}
    return updates

def route_orchestrator(state: AgentState) -> Literal["slack_agent", "jira_agent", "approval_agent", "__end__"]:
    next_step = state.get('next', 'finish')
    if next_step == 'slack':
        logging.info("ORCHESTRATOR: Routing to Slack agent.")
        return "slack_agent"
    elif next_step == 'jira':
        logging.info("ORCHESTRATOR: Routing to Jira agent.")
        return "jira_agent"
    elif next_step == 'approval':
        logging.info("ORCHESTRATOR: Routing to Approval agent.")
        return "approval_agent"
    else:
        logging.info("ORCHESTRATOR: Finishing workflow.")
        return "__end__"

def slack_agent(state: AgentState):
    """Slack-specific agent logic."""
    logging.info("SLACK_AGENT: Deciding action...")
    system_prompt = """
You are a Slack agent handling communication tasks.
Use your tools to send messages or perform Slack-related actions as needed.
If ticket_status == 'closed', send a Slack message notifying the approval using send_slack_message tool.
Respond based on the current state and messages.
"""
    state_summary = f"""
Current state:
ticket_id: {state.get('ticket_id')}
approver: {state.get('approver')}
approval_status: {state.get('approval_status')}
ticket_status: {state.get('ticket_status')}
slack_thread_ts: {state.get('slack_thread_ts')}
slack_notification_sent: {state.get('slack_notification_sent', False)}
"""
    system_message = SystemMessage(content=system_prompt + state_summary)
    messages = [system_message] + state['messages']
    response = slack_llm.invoke(messages)
    logging.info(f"SLACK_AGENT: LLM Response: {response.content} | Tools: {response.tool_calls}")
    return {"messages": [response]}

def jira_agent(state: AgentState):
    """Jira-specific agent logic."""
    logging.info("JIRA_AGENT: Deciding action...")
    system_prompt = """
You are a Jira agent handling ticket creation, approvals, and related tasks.
Use your tools to interact with Jira as needed.
If approval_status == 'pending', check status using the appropriate tool.
If approval_status == 'approved' and ticket_status != 'closed', use grant_access_and_close_ticket.
Respond based on the current state and messages.
"""
    state_summary = f"""
Current state:
ticket_id: {state.get('ticket_id')}
approver: {state.get('approver')}
approval_status: {state.get('approval_status')}
ticket_status: {state.get('ticket_status')}
slack_thread_ts: {state.get('slack_thread_ts')}
slack_notification_sent: {state.get('slack_notification_sent', False)}
"""
    system_message = SystemMessage(content=system_prompt + state_summary)
    messages = [system_message] + state['messages']
    if state.get('approval_status') == 'pending' and state['ticket_id']:
        # Force check_approval_status tool call if pending
        tool_call = {
            'id': 'forced_check',
            'name': 'check_approval_status',
            'args': {'ticket_id': state['ticket_id'], 'approver': state.get('approver')}
        }
        response = AIMessage(content="Checking approval status...", tool_calls=[tool_call])
    else:
        response = jira_llm.invoke(messages)
    logging.info(f"JIRA_AGENT: LLM Response: {response.content} | Tools: {response.tool_calls}")
    return {"messages": [response]}

def approval_agent(state: AgentState):
    """Approval-specific agent to compute approvers and check approval status."""
    logging.info("APPROVAL_AGENT: Deciding action...")
    system_prompt = """
You are an Approval agent focused on approval discovery and status tracking for access requests.
Use your tools from Jira as needed:
- Use find_approvers_and_notify to determine the required approvers (from hierarchy) and notify them.
- Use check_approval_status to check who has approved and who is pending.
If approval is complete, the orchestrator/Jira agent will handle granting access and closing.
Respond with tool calls as appropriate based on current state and messages.
"""
    state_summary = f"""
Current state:
ticket_id: {state.get('ticket_id')}
approver: {state.get('approver')}
approval_status: {state.get('approval_status')}
ticket_status: {state.get('ticket_status')}
slack_thread_ts: {state.get('slack_thread_ts')}
slack_notification_sent: {state.get('slack_notification_sent', False)}
"""
    system_message = SystemMessage(content=system_prompt + state_summary)
    messages = [system_message] + state['messages']

    # If we know there's a pending approval and have a ticket, force a status check
    if state.get('ticket_id') and state.get('approval_status') != 'approved':
        tool_call = {
            'id': 'approval_forced_check',
            'name': 'check_approval_status',
            'args': {'ticket_id': state['ticket_id']}
        }
        response = AIMessage(content="Checking approval status...", tool_calls=[tool_call])
    else:
        # Let the LLM decide which Jira tool to call for approval tasks
        response = jira_llm.invoke(messages)
    logging.info(f"APPROVAL_AGENT: LLM Response: {response.content} | Tools: {response.tool_calls}")
    return {"messages": [response]}

def should_continue(state: AgentState) -> Literal["tools", "continue"]:
    """Determines whether to continue with tools or return to orchestrator."""
    last_message = state['messages'][-1]
    if last_message.tool_calls:
        logging.info("AGENT: Tool call detected, continuing to tools.")
        return "tools"
    else:
        logging.info("AGENT: No tool calls, returning to orchestrator.")
        return "continue"

def update_state_after_jira(state: AgentState):
    last_message = state['messages'][-1]  # Tool response
    if isinstance(last_message.content, str):
        try:
            result = json.loads(last_message.content)
            updates = {}
            if 'status' in result:
                status = result['status']
                # Normalize tool statuses into orchestrator-friendly states
                if status in ['fully_approved', 'approved']:
                    updates['approval_status'] = 'approved'
                elif status in ['partially_approved', 'success']:
                    # success from find_approvers_and_notify means approvals requested
                    updates['approval_status'] = 'pending'
                    updates['last_check_time'] = time.time()
                elif status in ['already_done']:
                    updates['approval_status'] = 'approved'
                    updates['ticket_status'] = 'closed'
                else:
                    updates['approval_status'] = status
            if 'ticket_id' in result:
                updates['ticket_id'] = result['ticket_id']
            if 'approver' in result:
                updates['approver'] = result['approver']
            if 'slack_thread_ts' in result:
                updates['slack_thread_ts'] = result['slack_thread_ts']
            if 'ticket_status' in result:
                updates['ticket_status'] = result['ticket_status']
            return updates
        except json.JSONDecodeError:
            pass
    return {}

def update_state_after_slack(state: AgentState):
    last_message = state['messages'][-1]  # Tool response
    if isinstance(last_message.content, str):
        try:
            result = json.loads(last_message.content)
            updates = {}
            if 'success' in result:
                updates['slack_notification_sent'] = True
            if 'slack_thread_ts' in result:
                updates['slack_thread_ts'] = result['slack_thread_ts']
            return updates
        except json.JSONDecodeError:
            pass
    # Assume success if no JSON
    return {'slack_notification_sent': True}

# 4. Graph Definition and Compilation
workflow = StateGraph(AgentState)

# Add nodes
workflow.add_node("orchestrator", orchestrator_agent)
workflow.add_node("slack_agent", slack_agent)
workflow.add_node("slack_tools", slack_tool_node)
workflow.add_node("update_after_slack", update_state_after_slack)
workflow.add_node("jira_agent", jira_agent)
workflow.add_node("approval_agent", approval_agent)
workflow.add_node("jira_tools", jira_tool_node)
workflow.add_node("update_after_jira", update_state_after_jira)

# Set entry point
workflow.set_entry_point("orchestrator")

# Orchestrator routing
workflow.add_conditional_edges(
    "orchestrator",
    route_orchestrator,
    {"slack_agent": "slack_agent", "jira_agent": "jira_agent", "approval_agent": "approval_agent", "__end__": END}
)

# Slack agent loop
workflow.add_conditional_edges(
    "slack_agent",
    should_continue,
    {"tools": "slack_tools", "continue": "orchestrator"}
)
workflow.add_edge("slack_tools", "update_after_slack")
workflow.add_edge("update_after_slack", "orchestrator")

# Jira agent loop
workflow.add_conditional_edges(
    "jira_agent",
    should_continue,
    {"tools": "jira_tools", "continue": "orchestrator"}
)
workflow.add_edge("jira_tools", "update_after_jira")
workflow.add_edge("update_after_jira", "orchestrator")

# Approval agent loop (uses Jira tools)
workflow.add_conditional_edges(
    "approval_agent",
    should_continue,
    {"tools": "jira_tools", "continue": "orchestrator"}
)

# Compile the graph with memory to remember conversation state
memory = MemorySaver()
app_graph = workflow.compile(checkpointer=memory)
app_graph.get_graph().print_ascii()

# To run the workflow with polling for Jira approval events
def run_with_polling(thread_id: str, initial_input: dict):
    config = {"configurable": {"thread_id": thread_id}}
    # Initial run to start the workflow
    app_graph.invoke(initial_input, config)
    while True:
        # Get current state
        checkpoint = memory.get(config)
        if checkpoint is None:
            break
        state = checkpoint['channel_values']
        if state.get('approval_status') != 'pending':
            logging.info("Workflow complete, stopping polling.")
            break
        time.sleep(60)  # Check every minute
        current_time = time.time()
        if current_time - state.get('last_check_time', 0) >= 300:
            logging.info("Polling for approval status.")
            poll_input = {"messages": []}  # Empty input to trigger orchestrator
            app_graph.invoke(poll_input, config)

# Example usage:
# run_with_polling("approval_thread_1", {"messages": [HumanMessage(content="User request: Need access to system X.")]})