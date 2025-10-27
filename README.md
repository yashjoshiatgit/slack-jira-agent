# AI-Powered Access Approval Agent

Autonomous agent that orchestrates IT access requests end‑to‑end using Slack, Jira, and LangGraph. Users ask for access in Slack, the agent opens and tracks a Jira ticket, notifies approvers, listens for Jira webhook updates, and closes the ticket when approvals are complete.

## Highlights

- Slack Socket Mode bot (no public inbound port needed for Slack)
- FastAPI webhook to receive Jira updates
- LangGraph state machine to coordinate tool calls
- Azure OpenAI (GPT‑4.1‑mini deployment) as the LLM

## Architecture

Runtime components and flow:

1) User mentions the bot in Slack → Slack Bolt handler seeds a new conversation in the agent graph.
2) Agent immediately acknowledges in Slack and creates a Jira ticket.
3) Agent identifies/contacts approvers (demo logic is configurable in code).
4) Jira webhook posts updates to our FastAPI endpoint when comments/changes happen.
5) Webhook message is injected into the same conversation state; the agent checks approval status and, when satisfied, grants access and closes the ticket.

Long‑lived listeners:
- Slack Socket Mode handler runs in the foreground and listens indefinitely.
- FastAPI+Uvicorn runs in a background thread and keeps the webhook endpoint active.

Each request is a self‑contained workflow that begins and ends, but the servers keep listening for new events.

## Project Structure

- `main.py` — Starts Slack Socket Mode and FastAPI (Jira webhook) together.
- `config.py` — Central wiring for Slack, Jira, LLM, FastAPI, and shared memory.
- `graph/agent.py` — LangGraph definition: state, tool node, orchestrator, and compiled app.
- `handlers/`
  - `slack_events.py` — Handles `app_mention` and kicks off a new workflow.
  - `jira_webhook.py` — FastAPI endpoint `/webhook` to ingest Jira updates.
- `tools/`
  - `jira_tools.py` — Create ticket, find approvers, check approvals, close ticket.
  - `communication.py` — Send Slack messages (and optional SMTP helper).
- `pyproject.toml` — Dependencies and project metadata (managed with uv/pip).

## Prerequisites

- Python 3.12+
- Slack App with Socket Mode enabled
- Jira Cloud project and API credentials
- Azure OpenAI resource and a model deployment named `gpt-4.1-mini` (or adjust in `config.py`)
- Optional: Ngrok (or any HTTPS tunnel) to expose the local FastAPI webhook to Jira Cloud

## Environment Variables (.env)

Create a `.env` file in the project root with at least:

- Slack
  - `SLACK_BOT_TOKEN` — Bot token (starts with `xoxb-`)
  - `SLACK_APP_TOKEN` — App‑level token for Socket Mode (starts with `xapp-`)
- Jira
  - `JIRA_URL` — Base URL, e.g. `https://your-domain.atlassian.net`
  - `JIRA_EMAIL` — Jira user email
  - `JIRA_API_TOKEN` — Jira API token
  - `JIRA_PROJECT_KEY` — Project key (optional; defaults to `OPS`)
- Azure OpenAI
  - `AZURE_API_KEY` — Azure OpenAI API key
  - The deployment name and endpoint are currently hardcoded in `config.py` (see notes below)
- Optional SMTP (if you wire real emails)
  - `SMTP_SERVER`, `SMTP_PORT`, `SENDER_EMAIL`, `SMTP_PASSWORD`

Example `.env`:

```env
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...

JIRA_URL=https://your-domain.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=your_jira_api_token
JIRA_PROJECT_KEY=OPS

AZURE_API_KEY=your_azure_openai_key

# Optional SMTP
SMTP_SERVER=smtp.example.com
SMTP_PORT=587
SENDER_EMAIL=bot@example.com
SMTP_PASSWORD=super-secret
```

## Installation

This repo uses a `pyproject.toml` with a `uv.lock`. You can use either uv or pip.

Using uv (recommended):

```powershell
# Install uv if needed: https://docs.astral.sh/uv/
uv sync
```

Using pip and venv:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -e .
```

Note: If you see import errors for FastAPI, Uvicorn, or Jira, install them explicitly:

```powershell
pip install fastapi uvicorn jira python-dotenv slack-sdk
```

## Slack App Configuration

1) Create a Slack app (from App Manifest or dashboard).
2) Enable Socket Mode and generate an App Token with scope `connections:write` (value starts with `xapp-`).
3) Install the app to your workspace and get a Bot Token (starts with `xoxb-`).
4) Scopes (at minimum):
   - `app_mentions:read` (Events)
   - `chat:write` (Post messages)
   - `users:read` and `users:read.email` (to look up requester email)
5) Subscribe to the `app_mention` event.

## Jira Webhook Configuration

Jira Cloud must reach your machine. Start a tunnel and configure the webhook.

1) Start a tunnel for port 8000 (e.g., Ngrok):

```powershell
ngrok http 8000
```

2) In Jira → System → Webhooks → Create:
   - URL: `https://<your-ngrok-id>.ngrok-free.app/webhook`
   - Events: “Issue updated” and “Comment created”

## Running the Application

```powershell
python main.py
```

What happens:
- A FastAPI server for the Jira webhook starts on `http://0.0.0.0:8000` in a background thread.
- The Slack Socket Mode handler starts in the main thread and keeps running.
- The process stays alive, listening for Slack mentions and Jira updates.

## Usage

In any channel where the bot is present:

1) Mention the bot, for example:
   - “@access-bot Please grant VPN access to alice@example.com”
2) The agent will:
   - Acknowledge in the same Slack thread.
   - Create a Jira ticket in `JIRA_PROJECT_KEY`.
   - Identify approvers (demo mapping) and “notify” them.
   - Wait for Jira comments with the word “Approved”.
3) When all required approvers have commented “Approved”, the agent will:
   - Transition the ticket to Done/Closed (best‑effort based on available transitions).
   - Post a final confirmation in the Slack thread.

## Customization

- Approver rules: Edit the demo mapping in `tools/jira_tools.py` inside `find_approvers_and_notify()` and `check_approval_status()`.
- LLM configuration: `config.py` uses `AzureChatOpenAI` with deployment `gpt-4.1-mini` and a fixed endpoint. Change these values in `config.py` to point to your Azure resource and deployment names.
- Project key: Set `JIRA_PROJECT_KEY` in your `.env` or keep the default `OPS`.
- Persistence: The in‑memory `active_workflows` map in `config.py` is for demos. For production, back it with Redis/DB to survive restarts and scale out.

## Troubleshooting

- “SLACK_APP_TOKEN environment variable not set or invalid!” on startup
  - Ensure `SLACK_APP_TOKEN` is set and begins with `xapp-`. Socket Mode must be enabled.
- Bot can’t DM/post or read emails
  - Check Slack scopes: `chat:write`, `users:read`, `users:read.email`, and that the app is installed to the workspace and the channel.
- Jira errors creating or transitioning issues
  - Verify `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, and that your account can create/transition issues in `JIRA_PROJECT_KEY`.
- ImportError for FastAPI/Uvicorn/Jira/dotenv
  - Install missing packages: `fastapi`, `uvicorn`, `jira`, `python-dotenv`, `slack-sdk`.
- Agent “ends workflow” after a step
  - That’s expected per request. The servers keep listening. When Jira posts a webhook update, the agent revives the same conversation (by Slack thread id) and continues.

## Notes

- Logging is set to INFO in `config.py`.
- The Google Generative AI import is present but not used; the active LLM is Azure OpenAI.
- For production, prefer managed secrets/storage, HTTPS, and idempotent tool implementations.
