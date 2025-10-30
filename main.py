import logging
import os
import threading
import uvicorn
from slack_bolt.adapter.socket_mode import SocketModeHandler

from config import fastapi_app, slack_app

# Import handlers to register the routes/listeners with the app instances.
# Even though they are not called directly here, this import ensures that
# the @fastapi_app.post and @slack_app.event decorators are executed and
# their functions are registered with the respective frameworks.

# Ensure handlers are imported so their decorators register listeners/routes.
import handlers.slack_events  # noqa: F401
import handlers.jira_webhook  # noqa: F401

if __name__ == "__main__":
    # Start the FastAPI server in a separate thread for the Jira webhook listener.
    # Using a daemon thread ensures it shuts down when the main application exits.
    uvicorn_thread = threading.Thread(
        target=uvicorn.run,
        args=(fastapi_app,),
        kwargs={"host": "0.0.0.0", "port": 8000},
        daemon=True
    )
    uvicorn_thread.start()
    logging.info("FastAPI server for Jira webhooks started on http://0.0.0.0:8000")

    # Start the Slack Socket Mode handler in the main thread.
    # This will block and listen for events from Slack indefinitely.
    app_token = os.getenv("SLACK_APP_TOKEN")
    if not app_token or not app_token.startswith("xapp-"):
        raise ValueError("SLACK_APP_TOKEN environment variable not set or invalid!")
        
    logging.info("Starting Slack SocketModeHandler...")
    SocketModeHandler(slack_app, app_token).start()