# /tools/communication.py
import logging
import os
import smtplib
from email.mime.text import MIMEText

from langchain_core.tools import tool

from config import slack_client # Import client from central config

@tool
def send_slack_message(channel: str, text: str, thread_ts: str) -> str:
    """
    Sends a message to a specific Slack channel and thread.
    Use this to communicate updates, confirmations, or errors to the user.
    """
    try:
        slack_client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
        logging.info(f"✅ Sent Slack message to {channel} (thread: {thread_ts})")
        return "Message sent successfully."
    except Exception as e:
        logging.error(f"❌ Failed to send Slack message: {e}")
        return f"Failed to send message: {e}"

def send_email(to: str, subject: str, body: str):
    """(Not a tool) Helper function to send an email using SMTP."""
    try:
        smtp_server = os.getenv("SMTP_SERVER")
        port = int(os.getenv("SMTP_PORT", 587))
        sender = os.getenv("SENDER_EMAIL")
        password = os.getenv("SMTP_PASSWORD")
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = to
        with smtplib.SMTP(smtp_server, port) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, to, msg.as_string())
        logging.info(f"Email sent to {to}")
    except Exception as e:
        logging.error(f"Failed to send email to {to}: {e}")