import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.ops_agent_service.config import get_ops_settings

settings = get_ops_settings()

# Strict Read-Only allowlists so agents CANNOT bypass HITL via MCP write tools
GMAIL_READ_TOOLS = ["search_emails", "read_email", "list_email_labels"]
DRIVE_READ_TOOLS = ["search", "get_file", "list_files"]
GITHUB_READ_TOOLS = ["list_issues", "list_pull_requests", "get_pull_request", "list_commits"]


@dataclass
class MCPToolBundle:
    gmail: Any = None
    drive: Any = None
    github: Any = None
    _stack: AsyncExitStack = field(default_factory=AsyncExitStack, repr=False)

    async def connect(self) -> None:
        if not settings.use_real_mcp:
            return
        from agno.tools.mcp import MCPTools

        google_token_exists = Path(settings.google_user_token_path).exists()
        google_env = {
            **os.environ,
            "GDRIVE_CREDENTIALS_PATH": settings.google_user_token_path
            if google_token_exists
            else settings.google_oauth_credentials_path,
            "GMAIL_CREDENTIALS_PATH": settings.google_user_token_path
            if google_token_exists
            else settings.google_oauth_credentials_path,
            "GOOGLE_OAUTH_CREDENTIALS": settings.google_oauth_credentials_path,
        }

        if settings.mcp_gmail_cmd:
            self.gmail = await self._stack.enter_async_context(
                MCPTools(command=settings.mcp_gmail_cmd, env=google_env, include_tools=GMAIL_READ_TOOLS)
            )
        if settings.mcp_drive_cmd:
            self.drive = await self._stack.enter_async_context(
                MCPTools(command=settings.mcp_drive_cmd, env=google_env, include_tools=DRIVE_READ_TOOLS)
            )
        if settings.mcp_github_cmd and settings.github_personal_access_token:
            github_env = {
                **os.environ,
                "GITHUB_PERSONAL_ACCESS_TOKEN": settings.github_personal_access_token,
            }
            self.github = await self._stack.enter_async_context(
                MCPTools(command=settings.mcp_github_cmd, env=github_env, include_tools=GITHUB_READ_TOOLS)
            )

    async def close(self) -> None:
        await self._stack.aclose()


def build_mcp_bundle() -> MCPToolBundle:
    return MCPToolBundle()
