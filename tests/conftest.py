from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auth as app_auth
from app import db as app_db
from app import hitl as app_hitl
from app import main as app_main
from app.rag import DriveRAGStore
from app.tools import request_gmail_draft


@dataclass
class _FakeRunResult:
    content: str


class FakeOpsSupervisor:
    """Deterministic supervisor double that triggers a real HITL enqueue without network calls."""

    async def arun(self, prompt: str) -> _FakeRunResult:
        request_gmail_draft(
            to="recruiter@startup.io",
            subject="Re: Back End AI Engineer - Technical Interview",
            body="שלום, אשמח לתאם שיחת היכרות טכנית השבוע. תודה רבה, עידן כזם.",
        )
        return _FakeRunResult(
            content=f"בריף בוקר הוכן עבור: '{prompt}'. הופקדה טיוטת מייל אחת לאישור HITL."
        )


@pytest.fixture()
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Bind SQLAlchemy engine & SessionLocal to a fresh per-test SQLite file."""
    db_file = tmp_path / "test_hitl.db"
    test_engine = create_engine(
        f"sqlite:///{db_file}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    TestSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    app_db.Base.metadata.create_all(bind=test_engine)

    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(app_db, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(app_hitl, "get_session", lambda: TestSessionLocal())
    monkeypatch.setattr(app_auth, "get_session", lambda: TestSessionLocal())
    yield test_engine
    test_engine.dispose()


@pytest.fixture()
def isolated_rag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DriveRAGStore:
    """Provide an isolated ChromaDB store inside tmp_path."""
    store = DriveRAGStore(
        persist_dir=str(tmp_path / "chroma_test"),
        collection_name="test_specs",
    )
    monkeypatch.setattr(app_main, "drive_rag_store", store)
    return store


@pytest.fixture()
def api_client(
    isolated_db: Any,
    isolated_rag: DriveRAGStore,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """FastAPI TestClient with isolated DB, isolated ChromaDB, and FakeOpsSupervisor."""
    monkeypatch.setattr(app_main, "build_ops_supervisor", lambda _bundle: FakeOpsSupervisor())
    with TestClient(app_main.app) as client:
        yield client
