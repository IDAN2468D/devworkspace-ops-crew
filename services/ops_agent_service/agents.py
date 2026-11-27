from typing import Any

from agno.agent import Agent
from agno.models.google import Gemini
from agno.team import Team

from services.ops_agent_service.config import get_ops_settings
from services.ops_agent_service.mcp_connectors import MCPToolBundle
from services.ops_agent_service.tools import (
    fetch_unread_emails_mock,
    index_doc_to_rag,
    list_drive_inbox_mock,
    list_open_github_items_mock,
    request_drive_organize,
    request_github_issue,
    request_gmail_draft,
    search_project_specs_rag,
    search_specs_elasticsearch,
)

settings = get_ops_settings()


def build_ops_supervisor(mcp_bundle: MCPToolBundle) -> Team:
    model = Gemini(id=settings.gemini_model_id, api_key=settings.google_api_key or None)

    gmail_tools: list[Any] = [mcp_bundle.gmail] if mcp_bundle.gmail else [fetch_unread_emails_mock]
    gmail_tools.append(request_gmail_draft)

    drive_tools: list[Any] = [mcp_bundle.drive] if mcp_bundle.drive else [list_drive_inbox_mock]
    drive_tools.extend(
        [search_project_specs_rag, search_specs_elasticsearch, index_doc_to_rag, request_drive_organize]
    )

    github_tools: list[Any] = [mcp_bundle.github] if mcp_bundle.github else [list_open_github_items_mock]
    github_tools.append(request_github_issue)

    gmail_agent = Agent(
        name="GmailTriageAgent",
        role="סורק מיילים שלא נקראו, מסווג דחיפות ומפקיד טיוטות תגובה ב-hitl-service",
        model=model,
        tools=gmail_tools,
        instructions=[
            "סווג מיילים לפי דחיפות (ראיונות עבודה, תקלות CI/CD, משימות).",
            "למיילים שדורשים תגובה, השתמש ב-request_gmail_draft להפקדת טיוטה לאישור אנושי.",
        ],
    )

    drive_agent = Agent(
        name="DriveLibrarianAgent",
        role="שולף הקשר ממסמכי SPEC דרך rag-service ו-search-service (Elasticsearch) ומציע סידור קבצים",
        model=model,
        tools=drive_tools,
        instructions=[
            "חפש הקשר סמנטי דרך search_project_specs_rag או חיפוש טקסט מלא דרך search_specs_elasticsearch.",
            "זהה קבצים בתיקיית Inbox והצע להעבירם דרך request_drive_organize.",
        ],
    )

    github_agent = Agent(
        name="GitHubDevOpsAgent",
        role="מנטר PRs, Issues וכישלונות CI ומציע פתיחת Issues ב-hitl-service",
        model=model,
        tools=github_tools,
        instructions=[
            "בדוק PRs פתוחים, Issues וכישלונות CI.",
            "אם זיהית תקלה שאין לה Issue פתוח, הפקד הצעה דרך request_github_issue.",
        ],
    )

    return Team(
        name="OpsSupervisor",
        mode="coordinate",
        model=model,
        members=[gmail_agent, drive_agent, github_agent],
        instructions=[
            "אתה המתזמר של DevWorkspace Ops Crew.",
            "הפעל את סוכני Gmail, Drive ו-GitHub לפי הצורך.",
            "אחד את הממצאים לסיכום קצר בעברית עם 3 פעולות מומלצות להיום.",
            "ציין במפורש אילו פעולות הועברו לתור ה-HITL וממתינות לאישור.",
        ],
        markdown=True,
    )
