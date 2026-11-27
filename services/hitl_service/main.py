from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from sqlalchemy import select

from services.hitl_service.config import get_hitl_settings
from services.hitl_service.db import PendingActionModel, get_hitl_session, init_hitl_db
from services.hitl_service.handlers import WRITE_HANDLERS
from shared.errors import register_error_handlers
from shared.events import (
    TOPIC_HITL_ACTION_DECIDED,
    TOPIC_HITL_ACTION_REQUESTED,
    KafkaEventBus,
)
from shared.schemas import ActionStatus, ApprovalDecision, EnqueueActionRequest, PendingAction

settings = get_hitl_settings()
event_bus = KafkaEventBus(
    service_name=settings.service_name,
    bootstrap_servers=settings.kafka_bootstrap_servers,
    enabled=settings.kafka_enabled,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_hitl_db()
    await event_bus.start_producer()
    try:
        yield
    finally:
        await event_bus.stop()


app = FastAPI(
    title="DevWorkspace HITL Service",
    version="2.0.0",
    description="Human-in-the-Loop Approval Gate Microservice with Kafka Events",
    lifespan=lifespan,
)
register_error_handlers(app, settings.service_name)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "service": settings.service_name,
        "status": "ok",
        "kafka_enabled": settings.kafka_enabled,
        "database_backend": "postgresql" if "postgresql" in settings.database_url else "sqlite",
    }


@app.post("/api/v1/hitl/enqueue", response_model=PendingAction)
async def enqueue_action(req: EnqueueActionRequest) -> PendingAction:
    now = datetime.now(timezone.utc)
    record = PendingActionModel(
        action_id=str(uuid4())[:8],
        agent_name=req.agent_name,
        action_type=req.action_type,
        summary=req.summary,
        payload=req.payload,
        status=ActionStatus.PENDING.value,
        created_at=now,
        updated_at=now,
    )
    with get_hitl_session() as session:
        session.add(record)
        session.commit()
        session.refresh(record)
        action = PendingAction.model_validate(record)

    await event_bus.publish(
        topic=TOPIC_HITL_ACTION_REQUESTED,
        event_type="hitl.action.requested",
        key=action.action_id,
        payload=action.model_dump(mode="json"),
    )
    return action


@app.get("/api/v1/hitl/pending", response_model=list[PendingAction])
def list_pending_actions() -> list[PendingAction]:
    with get_hitl_session() as session:
        stmt = (
            select(PendingActionModel)
            .where(PendingActionModel.status == ActionStatus.PENDING.value)
            .order_by(PendingActionModel.created_at.desc())
        )
        rows = session.scalars(stmt).all()
        return [PendingAction.model_validate(r) for r in rows]


@app.get("/api/v1/hitl/{action_id}", response_model=PendingAction)
def get_action(action_id: str) -> PendingAction:
    with get_hitl_session() as session:
        record = session.get(PendingActionModel, action_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"Action '{action_id}' not found")
        return PendingAction.model_validate(record)


@app.post("/api/v1/hitl/{action_id}/decide", response_model=PendingAction)
async def decide_action(action_id: str, decision: ApprovalDecision) -> PendingAction:
    with get_hitl_session() as session:
        record = session.get(PendingActionModel, action_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"Action '{action_id}' not found")
        if record.status != ActionStatus.PENDING.value:
            raise HTTPException(
                status_code=409,
                detail=f"Action '{action_id}' is already '{record.status}'",
            )

        record.decision_note = decision.note
        record.updated_at = datetime.now(timezone.utc)

        if not decision.approve:
            record.status = ActionStatus.REJECTED.value
            session.commit()
            session.refresh(record)
            action = PendingAction.model_validate(record)
        else:
            handler = WRITE_HANDLERS.get(record.action_type)
            if not handler:
                raise HTTPException(
                    status_code=500,
                    detail=f"No execution handler registered for '{record.action_type}'",
                )
            try:
                record.status = ActionStatus.APPROVED.value
                record.execution_result = handler(record.payload)
                record.status = ActionStatus.EXECUTED.value
            except Exception as exc:
                record.status = ActionStatus.FAILED.value
                record.execution_result = {"error": str(exc)}
                session.commit()
                session.refresh(record)
                raise HTTPException(
                    status_code=500,
                    detail=f"Execution failed for '{action_id}': {exc}",
                ) from exc

            session.commit()
            session.refresh(record)
            action = PendingAction.model_validate(record)

    await event_bus.publish(
        topic=TOPIC_HITL_ACTION_DECIDED,
        event_type="hitl.action.decided",
        key=action.action_id,
        payload=action.model_dump(mode="json"),
    )
    return action
