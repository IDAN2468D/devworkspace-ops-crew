from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth as app_auth


def test_google_login_generates_oauth_url_with_mcp_scopes(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(app_auth.settings, "google_client_id", "test-client-id.apps.googleusercontent.com")

    resp = api_client.get("/api/v1/auth/google/login")
    assert resp.status_code == 200
    data = resp.json()
    assert "accounts.google.com/o/oauth2/v2/auth" in data["auth_url"]
    assert "gmail.readonly" in data["auth_url"]
    assert "drive.readonly" in data["auth_url"]
    assert len(data["state"]) > 10


def test_google_callback_registers_user_saves_mcp_token_and_authenticates_me(
    api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    token_file = tmp_path / "google_user_token.json"
    monkeypatch.setattr(app_auth.settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(app_auth.settings, "google_client_secret", "test-client-secret")
    monkeypatch.setattr(app_auth.settings, "google_user_token_path", str(token_file))

    async def fake_exchange(_code: str):
        return (
            {
                "access_token": "ya29.fake-access-token",
                "refresh_token": "1//fake-refresh-token",
                "expires_in": 3600,
                "scope": "openid email profile https://www.googleapis.com/auth/gmail.readonly",
            },
            {
                "sub": "google-sub-12345",
                "email": "user@example.com",
                "name": "Idan Test",
                "picture": "https://example.com/avatar.png",
            },
        )

    monkeypatch.setattr(app_auth, "_exchange_google_code", fake_exchange)

    # 1. Start login to obtain valid CSRF state
    login_resp = api_client.get("/api/v1/auth/google/login")
    state = login_resp.json()["state"]

    # 2. First callback registers the user and persists MCP token JSON
    cb_resp = api_client.get(
        "/api/v1/auth/google/callback",
        params={"code": "fake-auth-code", "state": state},
    )
    assert cb_resp.status_code == 200
    cb_data = cb_resp.json()
    assert cb_data["is_new_user"] is True
    assert cb_data["google_mcp_token_saved"] is True
    assert cb_data["user"]["email"] == "user@example.com"
    assert cb_data["user"]["google_mcp_connected"] is True
    assert token_file.exists()

    jwt_token = cb_data["access_token"]

    # 3. Call /auth/me with Bearer JWT
    me_resp = api_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "user@example.com"

    # 4. Repeat login updates existing user instead of registering a duplicate
    state_2 = api_client.get("/api/v1/auth/google/login").json()["state"]
    cb_resp_2 = api_client.get(
        "/api/v1/auth/google/callback",
        params={"code": "fake-auth-code-2", "state": state_2},
    )
    assert cb_resp_2.status_code == 200
    assert cb_resp_2.json()["is_new_user"] is False


def test_me_without_token_returns_401(api_client: TestClient):
    resp = api_client.get("/api/v1/auth/me")
    assert resp.status_code == 401
