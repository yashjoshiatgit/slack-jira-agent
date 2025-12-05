import threading
from langchain_core.messages import HumanMessage
from config import slack_app
from graph.agent import workflow

@slack_app.event("app_mention")
def handle_app_mention(body, say):
    event = body["event"]
    ts = event.get("thread_ts") or event.get("ts")
    
    try:
        profile = slack_app.client.users_info(user=event["user"])["user"]["profile"]
        email = profile.get("email")

        if not email:
            return say("Error: Could not retrieve email. (Is this a bot?)", thread_ts=ts)
        state = {
            "messages": [HumanMessage(content=f"Request: {event['text']}\nEmail: {email}\nChannel ID: {event['channel']}\nThread TS: {ts}")],
            "channel_id": event["channel"],
            "thread_ts": ts,
            "user_email": email
        }
        threading.Thread(
            target=workflow.invoke, args=(state, {"configurable": {"thread_id": f"slack-{ts}"}}), daemon=True
        ).start()

    except Exception as e:
        say(f"System Error: {str(e)}", thread_ts=ts)