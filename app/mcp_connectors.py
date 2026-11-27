import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path

from agno.tools.mcp import MCPTools

from app.config import get_settings

settings = get_settings()

# Strict Read-Only allowlists so agents CANNOT bypass HITL via MCP write tools
GMAIL_READ_TOOLS = ["search_emails", "read_email", "list_email_labels"]
DRIVE_READ_TOOLS = ["search", "get_file", "list_files"]
GITHUB_READ_TOOLS = ["list_issues", "list_pull_requests", "get_pull_request", "list_commits"]


@dataclass
class MCPToolBundle:
    gmail: MCPTools | None = None
    drive: MCPTools | None = None
    github: MCPTools | None = None
    _stack: AsyncExitStack = field(default_factory=AsyncExitStack, repr=False)

    async def connect(self) -> None:
        """Open stdio MCP connections using the signed-in Google user token & GitHub PAT."""
        if not settings.use_real_mcp:
            return

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
            gmail_mcp = MCPTools(
                command=settings.mcp_gmail_cmd,
                env=google_env,
                include_tools=GMAIL_READ_TOOLS,
            )
            self.gmail = await self._stack.enter_async_context(gmail_mcp)

        if settings.mcp_drive_cmd:
            drive_mcp = MCPTools(
                command=settings.mcp_drive_cmd,
                env=google_env,
                include_tools=DRIVE_READ_TOOLS,
            )
            self.drive = await self._stack.enter_async_context(drive_mcp)

        if settings.mcp_github_cmd and settings.github_personal_access_token:
            github_env = {
                **os.environ,
                "GITHUB_PERSONAL_ACCESS_TOKEN": settings.github_personal_access_token,
            }
            github_mcp = MCPTools(
                command=settings.mcp_github_cmd,
                env=github_env,
                include_tools=GITHUB_READ_TOOLS,
            )
            self.github = await self._stack.enter_async_context(github_mcp)

    async def close(self) -> None:
        await self._stack.aclose()


def build_mcp_bundle() -> MCPToolBundle:
    return MCPToolBundle()
