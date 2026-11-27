import json

import httpx

from services.ops_agent_service.config import get_ops_settings

settings = get_ops_settings()


# ============================================================
# 1. Fallback Read-Only Tools (used when USE_REAL_MCP=false)
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
# 2. Inter-Service HTTP Tools (RAG Service + Search Service)
# ============================================================

def search_project_specs_rag(query: str, project: str | None = None, top_k: int = 4) -> str:
    """Semantic search over indexed SPEC docs via rag-service."""
    params: dict[str, str | int] = {"q": query, "top_k": top_k}
    if project:
        params["project"] = project
    resp = httpx.get(f"{settings.rag_service_url}/api/v1/rag/search", params=params, timeout=10.0)
    resp.raise_for_status()
    return resp.text


def search_specs_elasticsearch(query: str, project: str | None = None, size: int = 5) -> str:
    """Full-text keyword search over SPEC docs via search-service (Elasticsearch)."""
    params: dict[str, str | int] = {"q": query, "size": size}
    if project:
        params["project"] = project
    resp = httpx.get(f"{settings.search_service_url}/api/v1/search/specs", params=params, timeout=10.0)
    resp.raise_for_status()
    return resp.text


def index_doc_to_rag(doc_id: str, title: str, content: str, project: str = "general") -> str:
    """Index or update a document via rag-service (which also publishes a Kafka event to Elasticsearch)."""
    resp = httpx.post(
        f"{settings.rag_service_url}/api/v1/rag/ingest",
        json={"doc_id": doc_id, "title": title, "content": content, "project": project},
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return f"Indexed '{title}' ({doc_id}) via rag-service ({data.get('chunks_indexed', 0)} chunks)."


# ============================================================
# 3. HITL-Gated Write Tools (delegate to hitl-service over HTTP)
# ============================================================

def _enqueue_hitl(agent_name: str, action_type: str, summary: str, payload: dict) -> str:
    resp = httpx.post(
        f"{settings.hitl_service_url}/api/v1/hitl/enqueue",
        json={
            "agent_name": agent_name,
            "action_type": action_type,
            "summary": summary,
            "payload": payload,
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    action_id = resp.json()["action_id"]
    return f"Queued for HITL approval via hitl-service (action_id={action_id})."


def request_gmail_draft(to: str, subject: str, body: str) -> str:
    """Queue a Gmail draft in hitl-service for human approval."""
    return _enqueue_hitl(
        agent_name="GmailTriageAgent",
        action_type="gmail_draft",
        summary=f"יצירת טיוטת מייל אל {to} בנושא '{subject}'",
        payload={"to": to, "subject": subject, "body": body},
    )


def request_drive_organize(file_id: str, target_folder: str) -> str:
    """Queue moving a Google Drive file in hitl-service for human approval."""
    return _enqueue_hitl(
        agent_name="DriveLibrarianAgent",
        action_type="drive_move",
        summary=f"העברת קובץ {file_id} לתיקייה '{target_folder}'",
        payload={"file_id": file_id, "target_folder": target_folder},
    )


def request_github_issue(repo: str, title: str, body: str) -> str:
    """Queue opening a GitHub Issue in hitl-service for human approval."""
    return _enqueue_hitl(
        agent_name="GitHubDevOpsAgent",
        action_type="github_issue",
        summary=f"פתיחת Issue חדש ב-{repo}: '{title}'",
        payload={"repo": repo, "title": title, "body": body},
    )
