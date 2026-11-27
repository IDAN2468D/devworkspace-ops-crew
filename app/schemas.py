from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class ActionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class UserProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    google_sub: str
    email: str
    name: str
    picture_url: str | None = None
    google_mcp_connected: bool = False
    created_at: datetime


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    is_new_user: bool
    user: UserProfile
    google_mcp_token_saved: bool


class PendingAction(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action_id: str
    agent_name: str
    action_type: str
    summary: str
    payload: dict[str, Any]
    status: ActionStatus = ActionStatus.PENDING
    decision_note: str | None = None
    execution_result: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OpsRunRequest(BaseModel):
    prompt: str = Field(
        default="הכן לי בריף בוקר מהמיילים, הדרייב (כולל חיפוש SPEC ב-RAG) ו-GitHub, והצע פעולות נדרשות.",
        description="הנחיה ל-Ops Supervisor",
    )


class OpsRunResponse(BaseModel):
    supervisor_output: str
    pending_actions: list[PendingAction]


class ApprovalDecision(BaseModel):
    approve: bool
    note: str | None = None


class RAGIngestRequest(BaseModel):
    doc_id: str = Field(..., examples=["drv_spec_001"])
    title: str = Field(..., examples=["DevWorkspace_SPEC_v1.md"])
    content: str = Field(..., min_length=10)
    project: str = Field(default="general", examples=["devworkspace-ops-crew"])
    source_url: str | None = None


class RAGChunk(BaseModel):
    chunk_id: str
    title: str
    project: str
    text: str
    distance: float | None = None


class RAGSearchResponse(BaseModel):
    query: str
    results: list[RAGChunk]
