from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query

from app.agents import build_ops_supervisor
from app.auth import get_current_user, get_optional_user
from app.auth import router as auth_router
from app.config import get_settings
from app.db import init_db
from app.hitl import hitl_service
from app.mcp_connectors import build_mcp_bundle
from app.rag import drive_rag_store
from app.schemas import (
    ApprovalDecision,
    OpsRunRequest,
    OpsRunResponse,
    PendingAction,
    RAGIngestRequest,
    RAGSearchResponse,
    UserProfile,
)

settings = get_settings()
state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    drive_rag_store.seed_sample_specs_if_empty()

    mcp_bundle = build_mcp_bundle()
    await mcp_bundle.connect()
    state["mcp_bundle"] = mcp_bundle
    state["supervisor"] = build_ops_supervisor(mcp_bundle)
    try:
        yield
    finally:
        await mcp_bundle.close()


app = FastAPI(
    title="DevWorkspace Ops Crew API",
    version="1.1.0",
    description="Multi-Agent Crew (Gmail, Drive + ChromaDB RAG, GitHub) with Google OAuth Login & SQLAlchemy HITL Gate",
    lifespan=lifespan,
)

app.include_router(auth_router)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "use_real_mcp": settings.use_real_mcp,
        "auth_required": settings.auth_required,
        "database_backend": "postgresql" if "postgresql" in settings.database_url else "sqlite",
    }


@app.post("/api/v1/mcp/google/reconnect")
async def reconnect_google_mcp(
    current_user: UserProfile = Depends(get_current_user),
) -> dict[str, Any]:
    """Hot-reconnect Google MCP servers using the signed-in user's saved OAuth token."""
    old_bundle = state.get("mcp_bundle")
    if old_bundle:
        await old_bundle.close()

    new_bundle = build_mcp_bundle()
    await new_bundle.connect()
    state["mcp_bundle"] = new_bundle
    state["supervisor"] = build_ops_supervisor(new_bundle)
    return {
        "reconnected": True,
        "user_email": current_user.email,
        "use_real_mcp": settings.use_real_mcp,
    }


@app.post("/api/v1/ops/run", response_model=OpsRunResponse)
async def run_ops_crew(
    req: OpsRunRequest,
    _user: UserProfile | None = Depends(get_optional_user),
) -> OpsRunResponse:
    supervisor = state["supervisor"]
    result = await supervisor.arun(req.prompt)
    return OpsRunResponse(
        supervisor_output=str(result.content),
        pending_actions=hitl_service.list_pending(),
    )


@app.get("/api/v1/hitl/pending", response_model=list[PendingAction])
def get_pending_actions(
    _user: UserProfile | None = Depends(get_optional_user),
) -> list[PendingAction]:
    return hitl_service.list_pending()


@app.post("/api/v1/hitl/{action_id}/decide", response_model=PendingAction)
def decide_action(
    action_id: str,
    decision: ApprovalDecision,
    _user: UserProfile | None = Depends(get_optional_user),
) -> PendingAction:
    try:
        return hitl_service.decide(
            action_id=action_id, approve=decision.approve, note=decision.note
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/v1/rag/ingest")
def ingest_spec_doc(
    req: RAGIngestRequest,
    _user: UserProfile | None = Depends(get_optional_user),
) -> dict[str, Any]:
    chunks = drive_rag_store.upsert_document(
        doc_id=req.doc_id,
        title=req.title,
        content=req.content,
        project=req.project,
        source_url=req.source_url,
    )
    return {"doc_id": req.doc_id, "chunks_indexed": chunks}


@app.get("/api/v1/rag/search", response_model=RAGSearchResponse)
def search_rag(
    q: str = Query(..., min_length=2),
    project: str | None = None,
    top_k: int = Query(default=4, ge=1, le=10),
    _user: UserProfile | None = Depends(get_optional_user),
) -> RAGSearchResponse:
    return RAGSearchResponse(
        query=q,
        results=drive_rag_store.search(query=q, top_k=top_k, project=project),
    )
