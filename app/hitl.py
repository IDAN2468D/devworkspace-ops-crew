from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.db import PendingActionModel, get_session
from app.schemas import ActionStatus, PendingAction


class HITLApprovalService:
    """Persistent HITL approval gate backed by SQLAlchemy (PostgreSQL / SQLite)."""

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}

    def register_handler(
        self, action_type: str, handler: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        self._handlers[action_type] = handler

    def enqueue(
        self, agent_name: str, action_type: str, summary: str, payload: dict[str, Any]
    ) -> PendingAction:
        now = datetime.now(UTC)
        record = PendingActionModel(
            action_id=str(uuid4())[:8],
            agent_name=agent_name,
            action_type=action_type,
            summary=summary,
            payload=payload,
            status=ActionStatus.PENDING.value,
            created_at=now,
            updated_at=now,
        )
        with get_session() as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return PendingAction.model_validate(record)

    def list_pending(self) -> list[PendingAction]:
        with get_session() as session:
            stmt = (
                select(PendingActionModel)
                .where(PendingActionModel.status == ActionStatus.PENDING.value)
                .order_by(PendingActionModel.created_at.desc())
            )
            rows = session.scalars(stmt).all()
            return [PendingAction.model_validate(r) for r in rows]

    def get_action(self, action_id: str) -> PendingAction | None:
        with get_session() as session:
            record = session.get(PendingActionModel, action_id)
            return PendingAction.model_validate(record) if record else None

    def decide(
        self, action_id: str, approve: bool, note: str | None = None
    ) -> PendingAction:
        with get_session() as session:
            record = session.get(PendingActionModel, action_id)
            if not record:
                raise KeyError(f"Action '{action_id}' not found")
            if record.status != ActionStatus.PENDING.value:
                raise ValueError(f"Action '{action_id}' is already '{record.status}'")

            record.decision_note = note
            record.updated_at = datetime.now(UTC)

            if not approve:
                record.status = ActionStatus.REJECTED.value
                session.commit()
                session.refresh(record)
                return PendingAction.model_validate(record)

            handler = self._handlers.get(record.action_type)
            if not handler:
                raise RuntimeError(f"No execution handler registered for '{record.action_type}'")

            try:
                record.status = ActionStatus.APPROVED.value
                record.execution_result = handler(record.payload)
                record.status = ActionStatus.EXECUTED.value
            except Exception as exc:
                record.status = ActionStatus.FAILED.value
                record.execution_result = {"error": str(exc)}
                session.commit()
                session.refresh(record)
                raise RuntimeError(f"Execution failed for '{action_id}': {exc}") from exc

            session.commit()
            session.refresh(record)
            return PendingAction.model_validate(record)


hitl_service = HITLApprovalService()
