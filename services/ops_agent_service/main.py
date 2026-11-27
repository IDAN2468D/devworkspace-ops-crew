from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from services.ops_agent_service.agents import build_ops_supervisor
from services.ops_agent_service.config import get_ops_settings
from services.ops_agent_service.mcp_connectors import build_mcp_bundle
from shared.errors import register_error_handlers
from shared.events import TOPIC_OPS_RUN_COMPLETED, KafkaEventBus
from shared.schemas import OpsRunRequest, OpsRunResponse, PendingAction

settings = get_ops_settings()
state: dict[str, Any] = {}
event_bus = KafkaEventBus(
    service_name=settings.service_name,
    bootstrap_servers=settings.kafka_bootstrap_servers,
    enabled=settings.kafka_enabled,
)


async def _fetch_pending_actions() -> list[PendingAction]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{settings.hitl_service_url}/api/v1/hitl/pending")
            resp.raise_for_status()
            return [PendingAction.model_validate(item) for item in resp.json()]
    except Exception:
        return []


@asynccontextmanager
async def lifespan(_: FastAPI):
    mcp_bundle = build_mcp_bundle()
    await mcp_bundle.connect()
    state["mcp_bundle"] = mcp_bundle
    state["supervisor"] = build_ops_supervisor(mcp_bundle)
    await event_bus.start_producer()
    try:
        yield
    finally:
        await event_bus.stop()
        await mcp_bundle.close()


app = FastAPI(
    title="DevWorkspace Ops Agent Service",
    version="2.0.0",
    description="Multi-Agent Crew (Agno + Gemini + Read-Only MCP) Microservice",
    lifespan=lifespan,
)
register_error_handlers(app, settings.service_name)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "service": settings.service_name,
        "status": "ok",
        "use_real_mcp": settings.use_real_mcp,
        "kafka_enabled": settings.kafka_enabled,
    }


@app.post("/api/v1/ops/run", response_model=OpsRunResponse)
async def run_ops_crew(req: OpsRunRequest) -> OpsRunResponse:
    supervisor = state["supervisor"]
    result = await supervisor.arun(req.prompt)
    output_text = str(result.content)
    pending = await _fetch_pending_actions()

    await event_bus.publish(
        topic=TOPIC_OPS_RUN_COMPLETED,
        event_type="ops.run.completed",
        payload={
            "prompt": req.prompt,
            "summary": output_text[:300],
            "pending_actions_count": len(pending),
        },
    )
    return OpsRunResponse(supervisor_output=output_text, pending_actions=pending)


@app.post("/api/v1/mcp/google/reconnect")
async def reconnect_google_mcp() -> dict[str, Any]:
    old_bundle = state.get("mcp_bundle")
    if old_bundle:
        await old_bundle.close()
    new_bundle = build_mcp_bundle()
    await new_bundle.connect()
    state["mcp_bundle"] = new_bundle
    state["supervisor"] = build_ops_supervisor(new_bundle)
    return {"reconnected": True, "use_real_mcp": settings.use_real_mcp}
