# /tools/jira_tools.py
import json
import logging
import os
from langchain.agents import create_agent
from langchain_core.tools import tool
from config import llm
from config import jira_client, active_workflows
from prompts.prompt import JIRA_SYSTEM_PROMPT

@tool
def create_issue(project_key: str, summary: str, description: str, issue_type: str = "Task", user_email: str = None, access_requested: str = None, slack_info: dict = None) -> str:
    """
    Creates a new issue in the specified tracking system (e.g., Jira).
    This is the first step in the access request sequence.
    Adds idempotency: checks if a ticket for this request/thread already exists.
    
    Args:
        project_key: The project identifier (e.g., "CPG").
        summary: A short description of the issue.
        description: The detailed body of the issue.
        issue_type: The type of issue (default: "Task").
        user_email: (optional) The email of the requester, for idempotency.
        access_requested: (optional) The resource requested, for idempotency.
        slack_info: (optional) Dict with 'channel' and 'thread_ts', for idempotency.
    Returns:
        JSON with the created or existing 'ticket_id' and 'ticket_link'.
    """
    logging.info(f"TOOL: create_issue for project {project_key}")
    try:
        # Idempotency: check if a ticket for this workflow already exists
        # Use slack_info (thread_ts) or (user_email + access_requested) as unique key
        existing_ticket_id = None
        if slack_info and 'thread_ts' in slack_info:
            for tid, wf in active_workflows.items():
                if wf.get('slack_thread_ts') == slack_info['thread_ts']:
                    existing_ticket_id = tid
                    break
        elif user_email and access_requested:
            for tid, wf in active_workflows.items():
                if wf.get('user_email') == user_email and wf.get('access_requested') == access_requested:
                    existing_ticket_id = tid
                    break
        if existing_ticket_id:
            # Check if ticket_created step is already marked
            steps = active_workflows[existing_ticket_id].setdefault('steps_completed', set())
            if 'ticket_created' in steps:
                ticket_link = f"{os.getenv('JIRA_URL')}/browse/{existing_ticket_id}"
                logging.info(f"Idempotency: Found existing ticket {existing_ticket_id} for this request (step already completed).")
                return json.dumps({
                    "status": "already_exists",
                    "ticket_id": existing_ticket_id,
                    "ticket_link": ticket_link,
                    "message": f"Issue already exists: {ticket_link}"
                })
            else:
                # Mark step as completed and return
                steps.add('ticket_created')
                ticket_link = f"{os.getenv('JIRA_URL')}/browse/{existing_ticket_id}"
                logging.info(f"Idempotency: Found existing ticket {existing_ticket_id} for this request (step now marked completed).")
                return json.dumps({
                    "status": "already_exists",
                    "ticket_id": existing_ticket_id,
                    "ticket_link": ticket_link,
                    "message": f"Issue already exists: {ticket_link}"
                })

        # Otherwise, create a new issue
        issue = jira_client.create_issue(
            project=project_key,
            summary=summary,
            description=description,
            issuetype={"name": issue_type},
        )
        ticket_id = issue.key
        ticket_link = f"{os.getenv('JIRA_URL')}/browse/{ticket_id}"

        # Initialize workflow memory with steps_completed
        active_workflows[ticket_id] = active_workflows.get(ticket_id, {})
        active_workflows[ticket_id].setdefault('steps_completed', set()).add('ticket_created')

        logging.info(f"Created Issue {ticket_id}")
        return json.dumps({
            "status": "success", 
            "ticket_id": ticket_id, 
            "ticket_link": ticket_link,
            "message": f"Issue {ticket_id} created: {ticket_link}"
        })
    except Exception as e:
        logging.error(f"Failed to create issue: {e}")
        return json.dumps({"status": "error", "message": f"Failed to create issue: {e}"})

@tool
def store_workflow_mapping(ticket_id: str, user_email: str, access_requested: str, slack_info: dict) -> str:
    """
    Stores the mapping between a new ticket and external communication channels 
    (Slack thread, user details) to track the active workflow. This links the ticket to the thread.
    
    Args:
        ticket_id: The ID of the issue to track.
        user_email: The email of the person who needs access.
        access_requested: The specific system or resource requested.
        slack_info: Dictionary containing 'channel' and 'thread_ts' for Slack.
        
    Returns:
        JSON confirmation of mapping status.
    """
    logging.info(f"TOOL: store_workflow_mapping for {ticket_id}")
    try:
        # Update or create workflow memory, preserving steps_completed
        wf = active_workflows.get(ticket_id, {})
        wf["slack_channel"] = slack_info['channel']
        wf["slack_thread_ts"] = slack_info['thread_ts']
        wf["user_email"] = user_email
        wf["access_requested"] = access_requested
        steps = wf.setdefault('steps_completed', set())
        steps.add('workflow_mapped')
        active_workflows[ticket_id] = wf

        return json.dumps({
            "status": "success",
            "ticket_id": ticket_id,
            "message": "Workflow mapping successfully stored."
        })
    except Exception as e:
        logging.error(f"Failed to store mapping: {e}")
        return json.dumps({"status": "error", "message": f"Failed to store mapping: {e}"})

@tool
def transition_issue_to_done(ticket_id: str, ticket_link: str) -> str:
    """
    Transitions a ticket to a final state like 'Done', 'Approved', or 'Resolved' and adds a final comment.
    This is the first step in the finalization sequence.
    
    Args:
        ticket_id: The ID of the ticket to transition.
        ticket_link: The full URL to the Jira ticket (for messaging).
        
    Returns:
        JSON with the status of the transition.
    """
    logging.info(f"TOOL: transition_issue_to_done for {ticket_id}")
    try:
        issue = jira_client.issue(ticket_id)
        if issue.fields.status.name.lower() in ["done", "closed", "resolved", "approved"]:
             return json.dumps({"status": "already_done", "message": f"Ticket {ticket_link} was already closed."})
            
        transitions = jira_client.transitions(ticket_id)
        done_transition = next((t for t in transitions if t['name'].lower() in ["done", "approve", "closed"]), None)
        comment_to_add = "==============>>All approvals received. Access granted. Closing ticket."
        
        if done_transition:
            jira_client.add_comment(ticket_id, comment_to_add)
            jira_client.transition_issue(ticket_id, done_transition['id'])
        else:
            jira_client.add_comment(ticket_id, "==============>>All approvals received. Could not find a 'Done' transition. Please close manually.")

        return json.dumps({
            "status": "success",
            "ticket_id": ticket_id,
            "message": f"Issue {ticket_id} successfully transitioned."
        })
    except Exception as e:
        logging.error(f"Failed to transition issue: {e}")
        return json.dumps({"status": "error", "message": str(e)})

@tool
def post_final_confirmation(ticket_id: str, ticket_link: str) -> str:
    """
    Posts a final success confirmation message to the external channel (e.g., Slack)
    and removes the ticket from the active workflow tracker.
    
    Args:
        ticket_id: The ID of the completed ticket.
        ticket_link: The URL to the completed ticket.
        
    Returns:
        JSON confirmation that the external message was sent.
    """
    logging.info(f"TOOL: post_final_confirmation for {ticket_id}")
    try:
        active_workflows.pop(ticket_id, None)
        
        return json.dumps({
            "status": "success",
            "ticket_id": ticket_id,
            "message": f"🚀 The access request in ticket {ticket_link} has been fully approved and closed!"
        })
    except Exception as e:
        logging.error(f"Failed to post confirmation: {e}")
        return json.dumps({"status": "error", "message": f"Failed to post confirmation: {e}"})


tools = [create_issue, store_workflow_mapping, transition_issue_to_done, post_final_confirmation]

Jira_Agent = create_agent(
    llm, 
    tools,
    system_prompt= JIRA_SYSTEM_PROMPT # This variable must now contain the content below
)