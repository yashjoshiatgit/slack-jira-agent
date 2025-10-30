# /handlers/slack_events.py
import logging
import threading
from langchain_core.messages import HumanMessage

from config import slack_app # Import from central config
from graph.agent import workflow # Import the compiled graph

@slack_app.event("app_mention")
def handle_app_mention(body, say):
    """
    This function is triggered when the Slack bot is @mentioned.
    It parses the user's request, constructs an initial prompt,
    and invokes the agent graph in a new thread.
    """
    event = body["event"]
    user_text = event.get("text", "")
    thread_ts = event.get("thread_ts", event["ts"])
    user_id = event.get("user")
    channel = event["channel"]

    try:
        # Get the email of the user who mentioned the bot
        user_info = slack_app.client.users_info(user=user_id)
        user_profile = user_info["user"]["profile"]
        # Try to get email from profile, fallback to real_name + domain, or use Slack user ID
        requester_email = user_profile.get("email")
        if not requester_email:
            # If no email in profile, try to extract from the message or use a placeholder
            real_name = user_profile.get("real_name", f"slack_user_{user_id}")
            requester_email = f"{real_name.replace(' ', '.').lower()}@company.com"
            logging.warning(f"No email found for Slack user {user_id}, using placeholder: {requester_email}")

        # This initial prompt is the first message in the agent's memory.
        # It provides all the necessary context to kick off the workflow.
        prompt = f"""
        A user has requested IT access. Your job is to orchestrate the entire approval workflow.
        
        Initial Request: "{user_text}"
        Requester's Email: {requester_email}
        Slack Info: channel='{channel}', thread_ts='{thread_ts}'

        Process this access request workflow:
        1. Acknowledge in Slack (ONE message only)
        2. Create Jira ticket
        3. Notify user in Slack with the ticket link
        4. Find and notify approvers
        
        Do not create duplicate tickets. Execute each step once.
        """
        
        initial_messages = [HumanMessage(content=prompt)]
        
        # The thread_id is crucial for maintaining separate state/memory for each conversation.
        config = {"configurable": {"thread_id": f"slack-{thread_ts}"}}
        
        # Run the graph in a separate thread. This is critical to avoid blocking
        # the Slack event handler, which must respond within 3 seconds.
        threading.Thread(target=workflow.invoke, args=({"messages": initial_messages}, config)).start()

    except Exception as e:
        logging.error(f"Error handling app mention: {e}", exc_info=True)
        say(f"An error occurred while processing your request: {e}", thread_ts=thread_ts)