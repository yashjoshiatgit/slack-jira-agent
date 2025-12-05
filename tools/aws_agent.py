import os
import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent
from prompts.prompt import AWS_SYSTEM_PROMPT
from config import llm

client = MultiServerMCPClient(
    {
        "awslabs.aws-api-mcp-server": {
        "transport" : "stdio",
        "command": "uvx",
        "args": [
            "awslabs.aws-api-mcp-server@latest"
        ],
        "env": {
            "AWS_PROFILE": os.environ.get("AWS_PROFILE", "default"),
            "AWS_REGION": os.environ.get("AWS_REGION", "us-east-1"),
        },
        }
    }
    )


# Should make one policy --> create the group --> assign into the group =---> .Add user into the group( if group avial add into that one )

async def _load_mcp_tools():
    tools = await client.get_tools()
    if not isinstance(tools, (list, tuple)):
        raise RuntimeError("client.get_tools() did not return a list/tuple of tools")
    return tools


# aws_core → EC2, S3, Lambda, CloudFormation, etc.

# aws_iam → IAM users, roles, policies, permissions

# aws-api-mcp-server → Full AWS API surface via MCP

TOOLS = asyncio.run(_load_mcp_tools())

AWS_Agent = create_agent(
    llm,
    TOOLS,
    system_prompt=AWS_SYSTEM_PROMPT,
)
