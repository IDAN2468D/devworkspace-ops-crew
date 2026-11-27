from fastapi.testclient import TestClient


def test_health_endpoint(api_client: TestClient):
    resp = api_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["use_real_mcp"] is False


def test_ops_run_queues_hitl_and_allows_approval(api_client: TestClient):
    run_resp = api_client.post(
        "/api/v1/ops/run",
        json={"prompt": "הכן בריף בוקר"},
    )
    assert run_resp.status_code == 200
    run_data = run_resp.json()
    assert "בריף בוקר" in run_data["supervisor_output"]
    assert len(run_data["pending_actions"]) == 1

    action_id = run_data["pending_actions"][0]["action_id"]

    # Verify it appears in GET /hitl/pending
    pending_resp = api_client.get("/api/v1/hitl/pending")
    assert pending_resp.status_code == 200
    assert any(a["action_id"] == action_id for a in pending_resp.json())

    # Approve and execute
    approve_resp = api_client.post(
        f"/api/v1/hitl/{action_id}/decide",
        json={"approve": True, "note": "מאושר לשמירה כטיוטה"},
    )
    assert approve_resp.status_code == 200
    approved = approve_resp.json()
    assert approved["status"] == "executed"
    assert approved["execution_result"]["created_draft"] is True

    # Second decision must return 409 Conflict
    conflict_resp = api_client.post(
        f"/api/v1/hitl/{action_id}/decide",
        json={"approve": False},
    )
    assert conflict_resp.status_code == 409


def test_decide_missing_action_returns_404(api_client: TestClient):
    resp = api_client.post(
        "/api/v1/hitl/does-not-exist/decide",
        json={"approve": True},
    )
    assert resp.status_code == 404


def test_rag_ingest_and_search_endpoints(api_client: TestClient):
    ingest_resp = api_client.post(
        "/api/v1/rag/ingest",
        json={
            "doc_id": "drv_test_99",
            "title": "GitHub_Webhook_SPEC.md",
            "project": "ops-crew",
            "content": "GitHub webhooks trigger OpsSupervisor whenever a CI workflow fails on main.",
        },
    )
    assert ingest_resp.status_code == 200
    assert ingest_resp.json()["chunks_indexed"] == 1

    search_resp = api_client.get(
        "/api/v1/rag/search",
        params={"q": "CI workflow fails webhook", "project": "ops-crew", "top_k": 3},
    )
    assert search_resp.status_code == 200
    payload = search_resp.json()
    assert len(payload["results"]) >= 1
    assert payload["results"][0]["title"] == "GitHub_Webhook_SPEC.md"
