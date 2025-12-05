import json
import logging
import os
from langchain_core.tools import tool
from config import llm
from langchain.agents import create_agent
from prompts.prompt import APPROVAL_SYSTEM_PROMPT
import json, re, logging

HIERARCHY_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "approval_hierarchy.json")


@tool
def get_managers(email: str) -> str:
    """Return the manager(s) responsible for the given email based on the approval hierarchy."""

    try:
        with open(HIERARCHY_PATH, "r") as f:
            hierarchy = json.load(f)

        managers_map = hierarchy.get("managers", {})
        fallback = hierarchy.get("fallback_approvers", [])
        result = []
        for manager, employees in managers_map.items():
            if email in employees:
                result.append(manager)
        if result:
            return json.dumps({"managers": result})
        if fallback:
            return json.dumps({"managers": fallback})
        return json.dumps({"managers": []})

    except Exception as e:
        logging.error(f"Error loading approval hierarchy: {e}")
        return json.dumps({"error": "Failed to read hierarchy", "details": str(e)})
    
# get_managers("yashjoshi1485@gmail.com")
# {
#   "managers": [
#     "vaishal2611@gmail.com",
#     "yash_22132@gmail.com"
#   ]
# }

 
tools = [get_managers]

Approval_Agent = create_agent(
    llm,
    tools,
    system_prompt=APPROVAL_SYSTEM_PROMPT
)