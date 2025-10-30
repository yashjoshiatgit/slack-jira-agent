import json
import logging
import os
from langchain_core.tools import tool
from config import jira_client, active_workflows,llm
from langchain.agents import create_agent
from tools.email_agent import send_email
from prompts.prompt import APPROVAL_SYSTEM_PROMPT

_APPROVAL_HIERARCHY_CACHE = None

def _load_approval_hierarchy():
    """Load approval hierarchy JSON from path or provide sensible defaults.
    Structure example:
    {
      "managers": { "manager@example.com": ["user1@example.com", "user2@example.com"] },
      "fallback_approvers": ["security@example.com"]
    }
    """
    global _APPROVAL_HIERARCHY_CACHE
    if _APPROVAL_HIERARCHY_CACHE is not None:
        return _APPROVAL_HIERARCHY_CACHE

    default_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "approval_hierarchy.json"))
    path = os.getenv("APPROVAL_HIERARCHY_PATH", default_path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            _APPROVAL_HIERARCHY_CACHE = json.load(f)
            logging.info(f"Loaded approval hierarchy from {path}")
    except Exception as e:
        logging.warning(f"Could not load approval hierarchy from {path}: {e}. Using built-in defaults.")
        _APPROVAL_HIERARCHY_CACHE = {
            "managers": {
                "yashjoshi.one@gmail.com": ["peauampeauam@gmail.com", "yash_22132@ldrp.ac.in"]
            },
            "fallback_approvers": ["yashjoshi1485@gmail.com"]
        }
    return _APPROVAL_HIERARCHY_CACHE

def _required_approvers_for_user(user_email: str) -> list:
    """Determine required approvers for a given requester email from hierarchy."""
    hierarchy = _load_approval_hierarchy()
    managers = hierarchy.get("managers", {})
    # Find a manager whose subordinates contain the user
    for manager, subs in managers.items():
        if any(user_email.lower() == s.lower() for s in subs):
            return [manager]
    # Fallback approvers when no mapping exists
    return list(hierarchy.get("fallback_approvers", []))

@tool
def get_required_approvers(user_email: str) -> str:
    """
    Determines the list of required approver emails for a given user based on the organization hierarchy.
    
    Args:
        user_email: The email of the person requesting access.
        
    Returns:
        JSON with 'required_approvers' list or an error.
    """
    logging.info(f"TOOL: get_required_approvers for {user_email}")
    try:
        approval_chain = _required_approvers_for_user(user_email)
        if not approval_chain:
            raise ValueError("No approvers found for this user.")

        return json.dumps({
            "status": "success",
            "required_approvers": approval_chain
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

@tool
def update_ticket_with_approvers(ticket_id: str, user_email: str, access_requested: str, approver_list: list) -> str:
    """
    Updates the Jira ticket's description with request details and the required approver list.
    
    Args:
        ticket_id: The ID of the Jira ticket.
        user_email: The email of the requester.
        access_requested: The specific system or resource requested.
        approver_list: List of emails of the required approvers.
        
    Returns:
        JSON confirmation.
    """
    logging.info(f"TOOL: update_ticket_with_approvers for {ticket_id}")
    try:
        # Re-creating the description logic from the old tool
        slack_channel = active_workflows[ticket_id].get('slack_channel', 'N/A')
        slack_thread_ts = active_workflows[ticket_id].get('slack_thread_ts', 'N/A')
        
        description = (
            f"Request from: {user_email}\n"
            f"Access requested: {access_requested}\n"
            f"Slack thread: {slack_channel}#{slack_thread_ts}\n"
            f"Required approvers: {','.join(approver_list)}"
        )
        jira_client.issue(ticket_id).update(fields={"description": description})
        
        return json.dumps({
            "status": "success",
            "message": f"Jira ticket {ticket_id} description updated with {len(approver_list)} approvers."
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

@tool
def notify_approvers(approver_list: list, ticket_id: str, user_email: str, ticket_link: str) -> str:
    """
    Sends email notifications to the specified approver list with the ticket link.
    
    Args:
        approver_list: List of email addresses to notify.
        ticket_id: The ID of the Jira ticket.
        user_email: The email of the requester.
        ticket_link: The full URL to the Jira ticket.
        
    Returns:
        JSON with the list of successfully notified approvers.
    """
    logging.info(f"TOOL: notify_approvers for {ticket_id}")
    notified = []
    try:
        for approver in approver_list:
            body = (f"Please review and approve the access request for {user_email}:\n"
                    f"{ticket_link}\n"
                    f"To approve, please add a comment containing the word 'Approved' on the ticket.")
            # Assuming send_email is a working function from tools.email_agent
            send_email(approver, f"Access Request: {ticket_id}", body)
            notified.append(approver)
            logging.info(f"NOTIFIED -> Approver: {approver} | Ticket: {ticket_id}")

        return json.dumps({
            "status": "success",
            "approvers_notified": notified,
            "message": f"Email notifications sent to {len(notified)} approvers."
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Failed to send email to all: {e}"})

@tool
def scan_ticket_for_approvals(ticket_id: str) -> str:
    """
    Checks a Jira ticket's comments and required approvers (from ticket description) 
    to determine the current approval status. This is the new, generic check.
    
    Args:
        ticket_id: The ID of the Jira ticket to check.
        
    Returns:
        JSON with approval status ('fully_approved', 'partially_approved') 
        and lists of approved/pending approvers.
    """
    logging.info(f"TOOL: scan_ticket_for_approvals for ticket {ticket_id}")
    try:
        issue = jira_client.issue(ticket_id)
        description_lines = issue.fields.description.split('\n')
        
        # 1. Extract required approvers from the ticket description
        required_line = next((line for line in description_lines if line.startswith("Required approvers:")), None)
        if not required_line:
            raise ValueError("Required approvers list not found in ticket description.")
            
        required_approvers = set(required_line.split(': ')[1].split(','))

        # 2. Extract approved users from comments
        approved_by = set()
        for comment in jira_client.comments(issue):
            if "approved" in comment.body.lower():
                # Assuming comment.author.emailAddress is the reliable source of the approver
                approved_by.add(comment.author.emailAddress)

        pending = list(required_approvers - approved_by)

        if required_approvers.issubset(approved_by):
            return json.dumps({
                "status": "fully_approved",
                "approved_by": list(approved_by),
                "required_approvers": list(required_approvers),
                "pending_approvers": []
            })
        else:
            return json.dumps({
                "status": "partially_approved",
                "approved_by": list(approved_by),
                "pending_approvers": pending,
                "required_approvers": list(required_approvers)
            })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


tools = [get_required_approvers, update_ticket_with_approvers, notify_approvers, scan_ticket_for_approvals]

Approval_Agent = create_agent(
    llm,
    tools,
    system_prompt=APPROVAL_SYSTEM_PROMPT
)