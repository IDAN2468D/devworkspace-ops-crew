import pytest

from app.hitl import HITLApprovalService
from app.schemas import ActionStatus


def test_enqueue_and_list_pending(isolated_db):
    service = HITLApprovalService()
    action = service.enqueue(
        agent_name="GmailTriageAgent",
        action_type="gmail_draft",
        summary="טיוטת מייל לראיון",
        payload={"to": "hr@company.io", "subject": "Interview", "body": "Hi"},
    )

    assert action.status == ActionStatus.PENDING
    pending = service.list_pending()
    assert len(pending) == 1
    assert pending[0].action_id == action.action_id


def test_approve_executes_registered_handler(isolated_db):
    service = HITLApprovalService()
    called_with: list[dict] = []

    def handler(payload: dict) -> dict:
        called_with.append(payload)
        return {"ok": True, "created_draft": True}

    service.register_handler("gmail_draft", handler)
    action = service.enqueue(
        agent_name="GmailTriageAgent",
        action_type="gmail_draft",
        summary="טיוטה",
        payload={"to": "dev@example.com"},
    )

    result = service.decide(action.action_id, approve=True, note="נראה מעולה")
    assert result.status == ActionStatus.EXECUTED
    assert result.decision_note == "נראה מעולה"
    assert result.execution_result == {"ok": True, "created_draft": True}
    assert len(called_with) == 1
    assert service.list_pending() == []


def test_reject_does_not_execute_handler(isolated_db):
    service = HITLApprovalService()
    called = False

    def handler(_payload: dict) -> dict:
        nonlocal called
        called = True
        return {"ok": True}

    service.register_handler("github_issue", handler)
    action = service.enqueue(
        agent_name="GitHubDevOpsAgent",
        action_type="github_issue",
        summary="פתיחת Issue",
        payload={"repo": "IDAN2468D/mcp-ai-ide-core", "title": "Fix CI"},
    )

    result = service.decide(action.action_id, approve=False, note="כבר תוקן")
    assert result.status == ActionStatus.REJECTED
    assert result.decision_note == "כבר תוקן"
    assert result.execution_result is None
    assert called is False
    assert service.list_pending() == []


def test_double_decision_raises_conflict(isolated_db):
    service = HITLApprovalService()
    action = service.enqueue(
        agent_name="DriveLibrarianAgent",
        action_type="drive_move",
        summary="העברת קובץ",
        payload={"file_id": "f1", "target_folder": "Specs"},
    )
    service.decide(action.action_id, approve=False)

    with pytest.raises(ValueError, match="already"):
        service.decide(action.action_id, approve=True)


def test_unknown_action_raises_key_error(isolated_db):
    service = HITLApprovalService()
    with pytest.raises(KeyError, match="not found"):
        service.decide("nonexistent", approve=True)


def test_handler_exception_marks_action_failed(isolated_db):
    service = HITLApprovalService()

    def broken_handler(_payload: dict) -> dict:
        raise ConnectionError("GitHub API timeout")

    service.register_handler("github_issue", broken_handler)
    action = service.enqueue(
        agent_name="GitHubDevOpsAgent",
        action_type="github_issue",
        summary="פתיחת Issue",
        payload={"repo": "org/repo", "title": "Bug"},
    )

    with pytest.raises(RuntimeError, match="GitHub API timeout"):
        service.decide(action.action_id, approve=True)

    saved = service.get_action(action.action_id)
    assert saved is not None
    assert saved.status == ActionStatus.FAILED
    assert "GitHub API timeout" in saved.execution_result["error"]
