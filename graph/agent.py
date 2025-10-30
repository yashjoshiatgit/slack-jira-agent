from fastapi import FastAPI, Request
from jira import JIRA
import json
from langgraph.graph import StateGraph, END
from pydantic import BaseModel
from dotenv import load_dotenv
import os
import time
import threading
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from langchain_google_genai import ChatGoogleGenerativeAI
import smtplib
from email.mime.text import MIMEText
import logging
from typing import TypedDict, Annotated, List, Optional
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage, HumanMessage 
from prompts.prompt import ORCHESTRATOR_PROMPT
from langchain_core.messages import AIMessage  

from config import llm
from tools.email_agent import Email_Agent
from tools.organization_agent import Approval_Agent
from tools.slack_agent import Slack_Agent
from tools.ticket_agent import Jira_Agent
from langchain.agents import create_agent

Orchestrator_Agent = create_agent(llm, [], system_prompt=ORCHESTRATOR_PROMPT)

class GraphState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    next: Optional[str]
    ticket_id: Optional[str]  
    approver: Optional[str]   
    approval_status: Optional[str]  
    ticket_status: Optional[str]  
    slack_thread_ts: Optional[str]  
    slack_notification_sent: Optional[bool]  
    last_check_time: Optional[float]
    task: Optional[str]
    iterations: Optional[int]

def slack_agent_node(state: GraphState):
    task = state.get("task", "Handle Slack communication")
    logging.info(f"SlackAgent: Processing state for task '{task}'")
    response = Slack_Agent.invoke({"messages": state["messages"]})
    return {"messages": response["messages"]}

def email_agent_node(state: GraphState):
    task = state.get("task", "Handle email communication")
    logging.info(f"EmailAgent: Processing state for task '{task}'")
    response = Email_Agent.invoke({"messages": state["messages"]})
    return {"messages": response["messages"]}

def ticket_agent_node(state: GraphState):
    task = state.get("task", "Handle Jira ticket operations")
    logging.info(f"TicketAgent: Processing state for task '{task}'")
    response = Jira_Agent.invoke({"messages": state["messages"]})
    return {"messages": response["messages"]}

def organization_agent_node(state: GraphState):
    task = state.get("task", "Handle approval workflow")
    logging.info(f"OrganizationAgent: Processing state for task '{task}'")
    response = Approval_Agent.invoke({"messages": state["messages"]})
    return {"messages": response["messages"]}

def _format_recent_results(messages: list[BaseMessage], limit: int = 10) -> str:  
    recent = messages[-limit:] if len(messages) > limit else messages
    lines = [f"{msg.__class__.__name__.replace('Message', '')}: {msg.content.strip()}" for msg in recent]
    return "\n".join(lines) or "None"

def orchestrator_node(state: GraphState) -> dict:
    # Extract task from state or infer from the first message
    task = state.get("task")
    if not task and state["messages"]:
        # Extract task from the first human message
        first_msg = state["messages"][0]
        task = first_msg.content if hasattr(first_msg, 'content') else "Process user request"
    else:
        task = task or "Process user request"
    
    messages = state["messages"]
    iterations = state.get("iterations", 0)

    if iterations >= 5:
        logging.warning("Orchestrator: Max iterations hit - ethical abort to avoid loops")
        return {"messages": [AIMessage(content="Orchestrator: Loop limit reached → END (safety)")], "next": "END", "iterations": iterations + 1}

    human_input = f"""
        Original Task: {task}

        Agent History (what's been done):
        {_format_recent_results(messages)}

        Routing Rules - Analyze history to determine NEXT step:
        - If ticket created BUT user not notified → SlackAgent
        - If ticket created BUT approvers not notified → OrganizationAgent
        - If approval comment received BUT not checked → OrganizationAgent
        - If fully approved BUT not closed → TicketAgent
        - If action just completed (tool call success) AND user notified → END
        - Do NOT route back to the same agent that just ran unless explicitly needed
        - Available agents: SlackAgent, EmailAgent, TicketAgent, OrganizationAgent, or END
        
        Respond with ONLY the exact agent name or END. No explanation.
    """
    # ORCHESTRATOR_PROMPT is a plain string template; avoid calling `.format_messages` on it.
    # The orchestrator agent already has the system prompt; here we just provide the human input.
    prompt = [HumanMessage(content=human_input)]

    valid_decisions = {
        "SLACKAGENT": "SlackAgent",
        "EMAILAGENT": "EmailAgent",
        "TICKETAGENT": "TicketAgent",
        "ORGANIZATIONAGENT": "OrganizationAgent",
        "END": "END",
    }
    decision = "END"
    for attempt in range(3):
        resp = Orchestrator_Agent.invoke({"messages": prompt})
        raw = resp["messages"][-1].content.strip().upper()
        if raw in valid_decisions:
            decision = valid_decisions[raw]
            break
        logging.warning(f"Orchestrator retry {attempt+1}: Invalid '{raw}' - retrying")

    logging.info(f"Orchestrator (ReAct Agent): Autonomous routing for '{task}' → {decision} (iteration {iterations}, history analyzed)")

    return {
        "messages": [AIMessage(content=f"Orchestrator: Self-decided '{task}' → {decision} (full autonomy)")],
        "next": decision,
        "iterations": iterations + 1,
    }

graph = StateGraph(GraphState)

graph.add_node("Orchestrator", orchestrator_node)
graph.add_node("SlackAgent", slack_agent_node)
graph.add_node("EmailAgent", email_agent_node)
graph.add_node("TicketAgent", ticket_agent_node)
graph.add_node("OrganizationAgent", organization_agent_node)

graph.add_conditional_edges(
    "Orchestrator",
    lambda s: s["next"],
    {
        "SlackAgent": "SlackAgent",
        "EmailAgent": "EmailAgent",
        "TicketAgent": "TicketAgent",
        "OrganizationAgent": "OrganizationAgent",
        "END": END,
    },
)

graph.add_edge("SlackAgent", "Orchestrator")
graph.add_edge("EmailAgent", "Orchestrator")
graph.add_edge("TicketAgent", "Orchestrator")
graph.add_edge("OrganizationAgent", "Orchestrator")

graph.set_entry_point("Orchestrator")
workflow = graph.compile()
workflow.get_graph().print_ascii()

if __name__ == "__main__":
    load_dotenv()

    initial_state = {
        "messages": [],
        "task": "create jira ticket for bug and notify slack #alerts",
        "iterations": 0
    }
    final_state = workflow.invoke(initial_state)
    print("\n=== FIXED AUTONOMOUS FINAL STATE (Full Chaining) ===")
    for k, v in final_state.items():
        print(f"{k}: {v}")
    print("\nEthical Hack Debrief: Logs now trace decisions (e.g., 'iteration 1: Ticket, 2: Slack'). "
          "Pentest tip: Feed adversarial tasks like 'create ticket OR spam slack' to test prompt robustness. "
          "Extend with tools (e.g., LangChain ReAct) for dynamic agent spawning - next-level autonomy!")