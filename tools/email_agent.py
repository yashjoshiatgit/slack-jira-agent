import os
import smtplib
from email.mime.text import MIMEText
import logging
from langchain_core.tools import tool
from langchain.agents import create_agent
from config import llm
from prompts.prompt import EMAIL_SYSTEM_PROMPT

@tool
def send_email(to: str, subject: str, body: str) -> str:
    """
    Send an email using SMTP. Use this to notify users or admins about access requests.
    
    Args:
        to: Recipient email address
        subject: Email subject line
        body: Plain text email body
    
    Returns:
        Status message
    """
    try:
        smtp_server = os.getenv("SMTP_SERVER")
        port = int(os.getenv("SMTP_PORT", 587))
        sender = os.getenv("SENDER_EMAIL")
        password = os.getenv("SMTP_PASSWORD")

        if not all([smtp_server, sender, password]):
            return "Error: SMTP configuration missing in environment variables."

        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = to

        with smtplib.SMTP(smtp_server, port) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, to, msg.as_string())

        logging.info(f"Email sent successfully to {to}")
        return f"Email sent to {to} with subject: '{subject}'"

    except Exception as e:
        logging.error(f"Failed to send email to {to}: {str(e)}")
        return f"Failed to send email: {str(e)}"
    
    
tools = [send_email]

Email_Agent = create_agent(
    llm,
    tools,
    system_prompt=EMAIL_SYSTEM_PROMPT
)