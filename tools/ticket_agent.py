import json
import logging
import os
from langchain.agents import create_agent
from langchain_core.tools import tool
from config import llm
from config import jira_client, active_workflows
from prompts.prompt import JIRA_SYSTEM_PROMPT
from dotenv import load_dotenv
load_dotenv()
import re


@tool
def create_issue(summary: str, description: str, issue_type: str = "Task") -> str:
    """Create a Jira issue and return a status that it has been created. No idempotency checks here."""
    project_key = os.getenv('JIRA_PROJECT_KEY')
    logging.info(f"TOOL: create_issue for project {project_key}")
    print("+++++++++++++++++++++++++ JIRA TOOL ++++++++++++++++++++++++++++")
    print(f"CREATE ISSUE def create_issue({summary}: str, {description}: str, {issue_type}: str = None) ")
    try:
        issue = jira_client.create_issue(
            project=project_key,
            summary=summary,
            description=description,
            issuetype={"name": issue_type}, 
        )
        ticket_id = issue.key  # Still capture ID for logging, but not returned
        logging.info(f"Created Issue {ticket_id}")
        return json.dumps({"status": "success", "message": f"Ticket has been created {ticket_id}"})
    except Exception as e:
        logging.error(f"Failed to create issue: {e}")
        return json.dumps({"status": "error", "message": str(e)})

@tool
def get_issue_status(ticket_id: str) -> str:
    """Return the status name for the given ticket."""
    print("+++++++++++++++++++++++++ JIRA TOOL ++++++++++++++++++++++++++++")
    try:
        issue = jira_client.issue(ticket_id)
        return json.dumps({"status": str(getattr(issue.fields, 'status').name)})
    except Exception as e:
        logging.error(f"Failed to get issue status: {e}")
        return json.dumps({"status": "unknown", "error": str(e)})
    
@tool
def get_issue_details(ticket_id: str) -> str:
    """Return the description for the Issue"""
    print("+++++++++++++++++++++++++ JIRA TOOL ++++++++++++++++++++++++++++")
    try:
        issue = jira_client.issue(ticket_id)
        description = getattr(issue.fields, 'description', "")
        return json.dumps({"Description": str(description)})
    except Exception as e:
        logging.error(f"Failed to get issue details {e}")
        return json.dumps({"Description": "unknown", "error": str(e)})
    
# @tool
# def add_comment(ticket_id: str, comment: str) -> str:
#     """Add a comment to the ticket (validate ticket_id first)."""
#     print("+++++++++++++++++++++++++ JIRA TOOL ++++++++++++++++++++++++++++")
#     # Basic sanity-check for Jira issue keys like PROJ-123
#     if not isinstance(ticket_id, str) or not re.match(r'^[A-Z][A-Z0-9]+-\d+$', ticket_id.strip()):
#         logging.warning(f"add_comment: rejecting invalid ticket_id '{ticket_id}'")
#         return json.dumps({"status": "error", "message": f"invalid ticket_id '{ticket_id}'"})

#     try:
#         jira_client.add_comment(ticket_id, comment)
#         return json.dumps({"status": "success"})
#     except Exception as e:
#         logging.error(f"Failed to add comment: {e}")
#         return json.dumps({"status": "error", "message": str(e)})   

@tool
def get_comment_details(ticket_id: str) -> str:
    """Return the latest commenter's email and the comment text."""
    print("+++++++++++++++++++++++++ JIRA TOOL ++++++++++++++++++++++++++++")
    try:
        issue = jira_client.issue(ticket_id)
        comments = issue.fields.comment.comments
        if not comments:
            return json.dumps({"commenter_email": None, "comment": None})
        latest = comments[-1]
        commenter_email = getattr(latest.author, 'emailAddress', None)
        comment_text = latest.body

        return json.dumps({
            "commenter_email": str(commenter_email),
            "comment": str(comment_text)
        })
    except Exception as e:
        logging.error(f"Failed to fetch comment details: {e}")
        return json.dumps({"commenter_email": "unknown", "comment": "unknown", "error": str(e)})
    
@tool
def approve_issue_ticket(ticket_id: str) -> str:
    """Approve a Jira ticket by transitioning it to Done status."""
    print("+++++++++++++++++++++++++ JIRA TOOL ++++++++++++++++++++++++++++")
    print(f"APPROVE ISSUE def approve_issue({ticket_id}: str)")
    
    if not isinstance(ticket_id, str) or not re.match(r'^[A-Z][A-Z0-9]+-\d+$', ticket_id.strip()):
        logging.warning(f"approve_issue: rejecting invalid ticket_id '{ticket_id}'")
        return json.dumps({"status": "error", "message": f"invalid ticket_id '{ticket_id}'"})
    
    try:
        issue = jira_client.issue(ticket_id)
        transitions = jira_client.transitions(issue)
        
        approve_transition = None
        for transition in transitions:
            if transition['name'].lower() == 'done':
                approve_transition = transition['id']
                break
        
        if approve_transition:
            jira_client.transition_issue(issue, approve_transition)
            logging.info(f"Approved Issue {ticket_id}")
            return json.dumps({"status": "success", "message": f"Ticket {ticket_id} has been approved"})
        else:
            available_transitions = [t['name'] for t in transitions]
            return json.dumps({
                "status": "error", 
                "message": f"Cannot approve. Available transitions: {', '.join(available_transitions)}"
            })
            
    except Exception as e:
        logging.error(f"Failed to approve issue: {e}")
        return json.dumps({"status": "error", "message": str(e)})


tools = [
    create_issue,
    get_issue_status,
    # add_comment,
    get_issue_details,
    get_comment_details,
    approve_issue_ticket,
]

Jira_Agent = create_agent(llm, tools, system_prompt=JIRA_SYSTEM_PROMPT)