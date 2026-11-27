from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.api_gateway import main as gateway_main
from services.hitl_service import db as hitl_db
from services.hitl_service import main as hitl_main
from services.rag_service import main as rag_main
from services.rag_service.store import DriveRAGStore
from services.search_service import main as search_main
from shared.events import (
    TOPIC_HITL_ACTION_DECIDED,
    TOPIC_HITL_ACTION_REQUESTED,
    TOPIC_RAG_DOCUMENT_INGESTED,
)


@pytest.fixture()
def isolated_hitl_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "ms_hitl.db"
    engine = create_engine(f"sqlite:///{db_file}", future=True, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    hitl_db.HITLBase.metadata.create_all(bind=engine)

    monkeypatch.setattr(hitl_db, "engine", engine)
    monkeypatch.setattr(hitl_db, "SessionLocal", SessionLocal)
    monkeypatch.setattr(hitl_main, "get_hitl_session", lambda: SessionLocal())
    hitl_main.event_bus.published_events.clear()
    hitl_main.event_bus._local_subscribers.clear()

    with TestClient(hitl_main.app) as client:
        yield client
    engine.dispose()


@pytest.fixture()
def isolated_search_client():
    search_main.es_indexer._memory_indices.clear()
    with TestClient(search_main.app) as client:
        yield client


@pytest.fixture()
def isolated_rag_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = DriveRAGStore(persist_dir=str(tmp_path / "ms_chroma"), collection_name="ms_specs")
    monkeypatch.setattr(rag_main, "drive_rag_store", store)
    rag_main.event_bus.published_events.clear()
    rag_main.event_bus._local_subscribers.clear()

    with TestClient(rag_main.app) as client:
        yield client


def test_hitl_microservice_publishes_kafka_events_and_indexes_in_search(
    isolated_hitl_client: TestClient,
    isolated_search_client: TestClient,
):
    # Wire local Kafka subscriber from search-service to hitl-service event bus
    hitl_main.event_bus.subscribe_local(
        [TOPIC_HITL_ACTION_REQUESTED, TOPIC_HITL_ACTION_DECIDED],
        search_main.handle_kafka_event,
    )

    enq = isolated_hitl_client.post(
        "/api/v1/hitl/enqueue",
        json={
            "agent_name": "GitHubDevOpsAgent",
            "action_type": "github_issue",
            "summary": "פתיחת Issue על כישלון CI",
            "payload": {"repo": "IDAN2468D/mcp-ai-ide-core", "title": "Fix CI", "body": "Details"},
        },
    )
    assert enq.status_code == 200
    action_id = enq.json()["action_id"]

    decided = isolated_hitl_client.post(
        f"/api/v1/hitl/{action_id}/decide",
        json={"approve": True, "note": "Approved in microservice test"},
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "executed"

    # Verify real-time Kafka -> Elasticsearch indexing in search-service
    events_resp = isolated_search_client.get(
        "/api/v1/search/events", params={"q": "hitl.action.decided"}
    )
    assert events_resp.status_code == 200
    assert events_resp.json()["total"] >= 1

    summary_resp = isolated_search_client.get("/api/v1/search/reports/summary")
    assert summary_resp.status_code == 200
    assert summary_resp.json()["indexed_events"] >= 2


def test_rag_ingest_streams_to_elasticsearch_search_service(
    isolated_rag_client: TestClient,
    isolated_search_client: TestClient,
):
    rag_main.event_bus.subscribe_local(
        [TOPIC_RAG_DOCUMENT_INGESTED],
        search_main.handle_kafka_event,
    )

    resp = isolated_rag_client.post(
        "/api/v1/rag/ingest",
        json={
            "doc_id": "spec_kafka_es_1",
            "title": "Kafka_Elasticsearch_Architecture.md",
            "project": "ops-crew",
            "content": "Real-time Kafka event streaming into Elasticsearch enables fast search and reporting.",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["chunks_indexed"] == 1

    # Search via Elasticsearch-backed search-service
    es_resp = isolated_search_client.get(
        "/api/v1/search/specs", params={"q": "Kafka Elasticsearch", "project": "ops-crew"}
    )
    assert es_resp.status_code == 200
    data = es_resp.json()
    assert data["total"] == 1
    assert data["hits"][0]["source"]["title"] == "Kafka_Elasticsearch_Architecture.md"


def test_centralized_error_handler_returns_structured_error(isolated_hitl_client: TestClient):
    resp = isolated_hitl_client.post(
        "/api/v1/hitl/nonexistent-id/decide",
        json={"approve": True},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["service"] == "hitl-service"
    assert body["error"]["code"] == 404


def test_api_gateway_enforces_jwt_when_auth_required(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gateway_main.settings, "auth_required", True)
    with TestClient(gateway_main.app) as client:
        resp = client.get("/api/v1/hitl/pending")
        assert resp.status_code == 401
        assert resp.json()["error"]["service"] == "api-gateway"
