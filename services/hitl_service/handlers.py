from collections.abc import Callable
from typing import Any

import httpx

from services.hitl_service.config import get_hitl_settings

settings = get_hitl_settings()


def _execute_gmail_draft(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "live" if settings.use_real_mcp else "dry_run",
        "created_draft": True,
        "to": payload["to"],
        "subject": payload["subject"],
    }


def _execute_drive_move(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "live" if settings.use_real_mcp else "dry_run",
        "moved": True,
        "file_id": payload["file_id"],
        "target_folder": payload["target_folder"],
    }


def _execute_github_issue(payload: dict[str, Any]) -> dict[str, Any]:
    repo = payload["repo"]
    title = payload["title"]
    body = payload["body"]

    if settings.github_personal_access_token and settings.use_real_mcp:
        resp = httpx.post(
            f"https://api.github.com/repos/{repo}/issues",
            headers={
                "Authorization": f"Bearer {settings.github_personal_access_token}",
                "Accept": "application/vnd.github+json",
            },
            json={"title": title, "body": body},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"mode": "live", "issue_number": data.get("number"), "url": data.get("html_url")}

    return {"mode": "dry_run", "created_issue": True, "repo": repo, "title": title}


WRITE_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "gmail_draft": _execute_gmail_draft,
    "drive_move": _execute_drive_move,
    "github_issue": _execute_github_issue,
}
