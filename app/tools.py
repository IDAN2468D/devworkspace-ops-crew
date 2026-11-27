import json
from typing import Any

import httpx

from app.config import get_settings
from app.hitl import hitl_service
from app.rag import drive_rag_store

settings = get_settings()


# ============================================================
# 1. Real Write Handlers (executed ONLY after HITL approval)
# ============================================================

def _execute_gmail_draft(payload: dict[str, Any]) -> dict[str, Any]:
    # In production, call Gmail API users().drafts().create(...) with OAuth token.
    return {
        "mode": "live" if settings.use_real_mcp else "dry_run",
        "created_draft": True,
        "to": payload["to"],
        "subject": payload["subject"],
    }


def _execute_drive_move(payload: dict[str, Any]) -> dict[str, Any]:
    # In production, call Google Drive API files().update(fileId=..., addParents=...).
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


hitl_service.register_handler("gmail_draft", _execute_gmail_draft)
hitl_service.register_handler("drive_move", _execute_drive_move)
hitl_service.register_handler("github_issue", _execute_github_issue)


# ============================================================
# 2. Fallback Read-Only Tools (used when USE_REAL_MCP=false)
# ============================================================

def fetch_unread_emails_mock(limit: int = 5) -> str:
    """Fetch recent unread Gmail messages (local fallback when USE_REAL_MCP=false)."""
    mock_emails = [
        {
            "id": "msg_101",
            "from": "recruiter@startup.io",
            "subject": "Back End AI Engineer - Technical Interview",
            "snippet": "נשמח לתאם שיחת היכרות טכנית השבוע על ניסיון ב-Python ו-Multi-Agent.",
        },
        {
            "id": "msg_102",
            "from": "notifications@github.com",
            "subject": "[mcp-ai-ide-core] CI workflow failed on main",
            "snippet": "Pytest failed on test_hitl_gate.py after PR #14.",
        },
    ]
    return json.dumps(mock_emails[:limit], ensure_ascii=False)


def list_drive_inbox_mock() -> str:
    """List recent files in Google Drive Inbox (local fallback when USE_REAL_MCP=false)."""
    mock_files = [
        {"file_id": "drv_spec_001", "name": "DevWorkspace_SPEC_v1.md", "folder": "Inbox"},
        {"file_id": "drv_notes_003", "name": "Interview_Prep_Notes.gdoc", "folder": "Inbox"},
    ]
    return json.dumps(mock_files, ensure_ascii=False)


def list_open_github_items_mock(repo: str = "") -> str:
    """List open Pull Requests and Issues in GitHub (local fallback when USE_REAL_MCP=false)."""
    target_repo = repo or settings.github_default_repo
    mock_items = {
        "repo": target_repo,
        "open_prs": [{"number": 14, "title": "Add HITL middleware", "status": "checks_failed"}],
        "open_issues": [{"number": 9, "title": "Refactor ChromaDB indexer"}],
    }
    return json.dumps(mock_items, ensure_ascii=False)


# ============================================================
# 3. ChromaDB RAG Tools (always active for DriveLibrarianAgent)
# ============================================================

def search_project_specs_rag(query: str, project: str | None = None, top_k: int = 4) -> str:
    """Semantic search over indexed Google Drive project SPEC and PRD documents in ChromaDB."""
    chunks = drive_rag_store.search(query=query, top_k=top_k, project=project)
    return json.dumps([c.model_dump() for c in chunks], ensure_ascii=False)


def index_doc_to_rag(doc_id: str, title: str, content: str, project: str = "general") -> str:
    """Index or update a Google Drive document in ChromaDB for semantic retrieval."""
    count = drive_rag_store.upsert_document(
        doc_id=doc_id, title=title, content=content, project=project
    )
    return f"Indexed '{title}' ({doc_id}) into ChromaDB ({count} chunks)."


# ============================================================
# 4. HITL-Gated Write Tools (never execute directly!)
# ============================================================

def request_gmail_draft(to: str, subject: str, body: str) -> str:
    """Queue a Gmail draft for human approval before creating it."""
    action = hitl_service.enqueue(
        agent_name="GmailTriageAgent",
        action_type="gmail_draft",
        summary=f"יצירת טיוטת מייל אל {to} בנושא '{subject}'",
        payload={"to": to, "subject": subject, "body": body},
    )
    return f"Queued for HITL approval (action_id={action.action_id})."


def request_drive_organize(file_id: str, target_folder: str) -> str:
    """Queue moving a Google Drive file to a target folder for human approval."""
    action = hitl_service.enqueue(
        agent_name="DriveLibrarianAgent",
        action_type="drive_move",
        summary=f"העברת קובץ {file_id} לתיקייה '{target_folder}'",
        payload={"file_id": file_id, "target_folder": target_folder},
    )
    return f"Queued for HITL approval (action_id={action.action_id})."


def request_github_issue(repo: str, title: str, body: str) -> str:
    """Queue opening a GitHub Issue for human approval."""
    action = hitl_service.enqueue(
        agent_name="GitHubDevOpsAgent",
        action_type="github_issue",
        summary=f"פתיחת Issue חדש ב-{repo}: '{title}'",
        payload={"repo": repo, "title": title, "body": body},
    )
    return f"Queued for HITL approval (action_id={action.action_id})."
