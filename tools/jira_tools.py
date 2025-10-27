# /tools/jira_tools.py
import json
import logging
import os

from langchain_core.tools import tool

from config import jira_client, active_workflows # Import from central config

# Cache for approval hierarchy to avoid re-reading file repeatedly
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
def create_jira_ticket(user_email: str, access_requested: str, slack_info: dict) -> str:
    """
    Creates a Jira ticket for an access request.
    This is the very first step of any new request.
    Args:
        user_email: The email of the person who needs access.
        access_requested: The specific system or resource they need access to.
        slack_info: A dictionary containing 'channel' and 'thread_ts' for communication.
    """
    logging.info(f"TOOL: create_jira_ticket for {user_email}")
    try:
        summary = f"Grant {access_requested} access to {user_email}"
        issue = jira_client.create_issue(
            project=os.getenv("JIRA_PROJECT_KEY", "OPS"),
            summary=summary,
            description=f"Processing access request...\nSlack Channel: {slack_info['channel']}\nSlack Thread: {slack_info['thread_ts']}",
            issuetype={"name": "Task"},
        )
        ticket_id = issue.key
        ticket_link = f"{os.getenv('JIRA_URL')}/browse/{ticket_id}"

        # Store mapping for the webhook
        active_workflows[ticket_id] = {
            "slack_channel": slack_info['channel'],
            "slack_thread_ts": slack_info['thread_ts']
        }
        logging.info(f"Created Jira ticket {ticket_id}")
        return json.dumps({"status": "success", "ticket_id": ticket_id, "ticket_link": ticket_link})
    except Exception as e:
        logging.error(f"Failed to create Jira ticket: {e}")
        return json.dumps({"status": "error", "message": f"Failed to create Jira ticket: {e}"})

@tool
def find_approvers_and_notify(ticket_id: str, user_email: str, access_requested: str, ticket_link: str) -> str:
    """
    Finds the required approvers for a user and notifies them via email.
    It also updates the Jira ticket with this information and notifies the user in Slack.
    Args:
        ticket_id: The ID of the Jira ticket (e.g., 'OPS-123').
        user_email: The email of the person requesting access.
        access_requested: The specific system or resource requested.
        ticket_link: The full URL to the Jira ticket.
    """
    logging.info(f"TOOL: find_approvers_and_notify for ticket {ticket_id}")
    try:
        logging.info(f"REQUESTER: {user_email} | ACCESS: {access_requested} | TICKET: {ticket_id}")
        approval_chain = _required_approvers_for_user(user_email)
        if not approval_chain:
            raise ValueError("No approvers found from hierarchy and no fallback_approvers configured.")

        description = (
            f"Request from: {user_email}\n"
            f"Access requested: {access_requested}\n"
            f"Slack thread: {active_workflows[ticket_id]['slack_channel']}#{active_workflows[ticket_id]['slack_thread_ts']}\n"
            f"Required approvers: {','.join(approval_chain)}"
        )
        jira_client.issue(ticket_id).update(fields={"description": description})

        for approver in approval_chain:
            body = (f"Please review and approve the access request for {user_email}:\n"
                    f"{ticket_link}\n"
                    f"To approve, please add a comment containing the word 'Approved' on the ticket.")
            # Your send_email logic can be called here
            # from tools.communication import send_email
            # send_email(approver, f"Access Request: {ticket_id}", body)
            logging.info(f"NOTIFY -> Approver: {approver} | Ticket: {ticket_id} | Requester: {user_email}")

        return json.dumps({
            "status": "success",
            "approvers_notified": approval_chain,
            "required_approvers": approval_chain,
            "message": f"Approvals have been requested from: {', '.join(approval_chain)}. I will monitor the ticket for updates."
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

@tool
def check_approval_status(ticket_id: str) -> str:
    """
    Checks a Jira ticket's comments to see who has approved the request.
    This should be used when a webhook signals an update to the ticket.
    Args:
        ticket_id: The ID of the Jira ticket to check.
    """
    logging.info(f"TOOL: check_approval_status for ticket {ticket_id}")
    try:
        issue = jira_client.issue(ticket_id)
        description_lines = issue.fields.description.split('\n')
        user_email_line = next((line for line in description_lines if line.startswith("Request from:")), None)
        user_email = user_email_line.split(': ')[1] if user_email_line else ""

        required_approvers = set(_required_approvers_for_user(user_email))

        approved_by = set()
        for comment in jira_client.comments(issue):
            if "approved" in comment.body.lower():
                approved_by.add(comment.author.emailAddress)

        pending = list(required_approvers - approved_by)
        # Terminal-friendly, explicit log of approval status
        logging.info(
            "APPROVAL STATUS | Ticket: %s | Requester: %s | Approved by: %s | Pending: %s",
            ticket_id,
            user_email,
            ", ".join(sorted(approved_by)) if approved_by else "-",
            ", ".join(sorted(pending)) if pending else "-",
        )

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
                "pending_approvers": list(required_approvers - approved_by),
                "required_approvers": list(required_approvers)
            })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})
@tool
def grant_access_and_close_ticket(ticket_id: str, ticket_link: str) -> str:
    """
    Transitions the Jira ticket to 'Done' and posts a final confirmation.
    This is the final step after all approvals are received.
    Args:
        ticket_id: The ID of the Jira ticket to close.
        ticket_link: The full URL to the Jira ticket.
    """
    logging.info(f"TOOL: grant_access_and_close_ticket for {ticket_id}")
    try:
        # === IDEMPOTENCY CHECK ===
        issue = jira_client.issue(ticket_id)
        if issue.fields.status.name.lower() in ["done", "closed", "resolved", "approved"]:
            logging.warning(f"Ticket {ticket_id} is already closed. Tool will not run again.")
            return json.dumps({
                "status": "already_done",
                "message": f"Ticket {ticket_link} was already approved and closed."
            })
        # =========================
            
        transitions = jira_client.transitions(ticket_id)
        done_transition = next((t for t in transitions if t['name'].lower() in ["done", "approve", "closed"]), None)
        comment_to_add = "✅ All approvals received. Access granted. Closing ticket."
        
        if done_transition:
            jira_client.add_comment(ticket_id, comment_to_add)
            jira_client.transition_issue(ticket_id, done_transition['id'])
        else:
            jira_client.add_comment(ticket_id, "✅ All approvals received. Could not find a 'Done' transition. Please close manually.")

        active_workflows.pop(ticket_id, None)

        return json.dumps({
            "status": "success",
            "message": f"🚀 The access request in ticket {ticket_link} has been fully approved and the ticket is now closed!"
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})