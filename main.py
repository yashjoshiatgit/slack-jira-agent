import logging
import os
import threading
import uvicorn
from slack_bolt.adapter.socket_mode import SocketModeHandler

from config import fastapi_app, slack_app
import handlers.slack_events  
import handlers.jira_webhook  

if __name__ == "__main__":
    uvicorn_thread = threading.Thread(
        target=uvicorn.run,
        args=(fastapi_app,),
        kwargs={"host": "0.0.0.0", "port": 8000},
        daemon=True
    )
    uvicorn_thread.start()
    logging.info("FastAPI server for Jira webhooks started on http://0.0.0.0:8000")
    app_token = os.getenv("SLACK_APP_TOKEN")
    if not app_token or not app_token.startswith("xapp-"):
        raise ValueError("SLACK_APP_TOKEN environment variable not set or invalid!")
        
    logging.info("Starting Slack SocketModeHandler...")
    SocketModeHandler(slack_app, app_token).start()