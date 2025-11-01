import logging
import os

from dotenv import load_dotenv
from jira import JIRA
from langchain_google_genai import ChatGoogleGenerativeAI
from fastapi import FastAPI
from slack_bolt import App
from slack_sdk import WebClient
from langchain_openai import AzureChatOpenAI
# 1. Basic Configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
load_dotenv()

# 2. Client Initializations
jira_client = JIRA(
    server=os.getenv("JIRA_URL"),
    basic_auth=(os.getenv("JIRA_EMAIL"), os.getenv("JIRA_API_TOKEN")),
)

llm = AzureChatOpenAI(
    azure_deployment="gpt-4.1-mini",
    api_version="2024-12-01-preview",
    azure_endpoint="https://slack-jira-day-resource.cognitiveservices.azure.com/",
    api_key=os.getenv("AZURE_API_KEY")
)

fastapi_app = FastAPI()
slack_app = App(token=os.getenv("SLACK_BOT_TOKEN"))
slack_client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))

# 3. Shared In-Memory State
# In a production system, this would be a database or Redis cache.
# Each workflow dict may include:
#   - slack_channel
#   - slack_thread_ts
#   - user_email
#   - access_requested
#   - steps_completed: set of completed step names (e.g., {"slack_ack", "ticket_created", "notified_approvers"})
active_workflows = {}