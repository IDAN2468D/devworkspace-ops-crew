import json
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import jwt
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from services.auth_service.config import get_auth_settings
from services.auth_service.db import UserModel, get_auth_session, init_auth_db
from shared.auth_utils import decode_jwt_token
from shared.errors import register_error_handlers
from shared.events import TOPIC_AUTH_USER_AUTHENTICATED, KafkaEventBus
from shared.schemas import AuthTokenResponse, UserProfile

settings = get_auth_settings()
event_bus = KafkaEventBus(
    service_name=settings.service_name,
    bootstrap_servers=settings.kafka_bootstrap_servers,
    enabled=settings.kafka_enabled,
)

bearer_scheme = HTTPBearer(auto_error=False)
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_OAUTH_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

_oauth_states: set[str] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_auth_db()
    await event_bus.start_producer()
    try:
        yield
    finally:
        await event_bus.stop()


app = FastAPI(
    title="DevWorkspace Auth Service",
    version="2.0.0",
    description="Dedicated Authentication & Google OAuth 2.0 Microservice",
    lifespan=lifespan,
)
register_error_handlers(app, settings.service_name)


def _create_jwt(user: UserModel) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user.user_id,
        "google_sub": user.google_sub,
        "email": user.email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _save_google_mcp_token_file(
    token_data: dict[str, Any],
    existing_refresh_token: str | None = None,
) -> Path:
    token_path = Path(settings.google_user_token_path)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_payload = {
        "type": "authorized_user",
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token") or existing_refresh_token,
        "token_uri": GOOGLE_TOKEN_URL,
        "scopes": token_data.get("scope", " ".join(GOOGLE_OAUTH_SCOPES)).split(),
    }
    token_path.write_text(json.dumps(mcp_payload, indent=2), encoding="utf-8")
    return token_path


def _to_user_profile(user: UserModel) -> UserProfile:
    return UserProfile(
        user_id=user.user_id,
        google_sub=user.google_sub,
        email=user.email,
        name=user.name,
        picture_url=user.picture_url,
        google_mcp_connected=bool(user.google_access_token or user.google_refresh_token),
        created_at=user.created_at,
    )


async def _exchange_google_code(code: str) -> tuple[dict[str, Any], dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Google token exchange failed: {token_resp.text}",
            )
        token_data = token_resp.json()
        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to fetch Google user profile",
            )
        return token_data, userinfo_resp.json()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    ops_session: str | None = Cookie(default=None),
) -> UserProfile:
    raw_token = credentials.credentials if credentials and credentials.credentials else ops_session
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token or ops_session cookie.",
        )
    decoded = decode_jwt_token(raw_token, settings.jwt_secret_key, settings.jwt_algorithm)
    user_id = decoded.get("sub")
    with get_auth_session() as session:
        user = session.get(UserModel, user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        return _to_user_profile(user)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "service": settings.service_name,
        "status": "ok",
        "kafka_enabled": settings.kafka_enabled,
        "database_backend": "postgresql" if "postgresql" in settings.database_url else "sqlite",
    }


@app.get("/api/v1/auth/google/login")
def google_login() -> dict[str, str]:
    if not settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GOOGLE_CLIENT_ID is not configured",
        )
    state = secrets.token_urlsafe(24)
    _oauth_states.add(state)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_OAUTH_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return {"auth_url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}", "state": state}


@app.get("/api/v1/auth/google/callback", response_model=AuthTokenResponse)
async def google_callback(
    response: Response,
    code: str = Query(...),
    state: str = Query(...),
) -> AuthTokenResponse:
    if state not in _oauth_states:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state parameter",
        )
    _oauth_states.discard(state)

    token_data, userinfo = await _exchange_google_code(code)
    google_sub = str(userinfo["sub"])
    email = str(userinfo["email"])
    name = str(userinfo.get("name") or email.split("@")[0])
    picture_url = userinfo.get("picture")
    expires_at = datetime.now(UTC) + timedelta(
        seconds=int(token_data.get("expires_in", 3600))
    )

    is_new_user = False
    with get_auth_session() as session:
        user = session.scalar(select(UserModel).where(UserModel.google_sub == google_sub))
        if user is None:
            is_new_user = True
            user = UserModel(
                user_id=str(uuid4()),
                google_sub=google_sub,
                email=email,
                name=name,
                picture_url=picture_url,
                google_access_token=token_data.get("access_token"),
                google_refresh_token=token_data.get("refresh_token"),
                google_scopes=token_data.get("scope", " ".join(GOOGLE_OAUTH_SCOPES)),
                token_expires_at=expires_at,
            )
            session.add(user)
        else:
            user.name = name
            user.picture_url = picture_url
            user.google_access_token = token_data.get("access_token")
            if token_data.get("refresh_token"):
                user.google_refresh_token = token_data["refresh_token"]
            user.google_scopes = token_data.get("scope", user.google_scopes)
            user.token_expires_at = expires_at
            user.updated_at = datetime.now(UTC)

        session.commit()
        session.refresh(user)
        _save_google_mcp_token_file(token_data, existing_refresh_token=user.google_refresh_token)
        jwt_token = _create_jwt(user)
        profile = _to_user_profile(user)

    await event_bus.publish(
        topic=TOPIC_AUTH_USER_AUTHENTICATED,
        event_type="auth.user.authenticated",
        key=profile.user_id,
        payload={
            "user_id": profile.user_id,
            "email": profile.email,
            "is_new_user": is_new_user,
        },
    )

    response.set_cookie(
        key="ops_session",
        value=jwt_token,
        httponly=True,
        samesite="lax",
        max_age=settings.jwt_expire_minutes * 60,
    )
    return AuthTokenResponse(
        access_token=jwt_token,
        is_new_user=is_new_user,
        user=profile,
        google_mcp_token_saved=True,
    )


@app.get("/api/v1/auth/me", response_model=UserProfile)
def get_me(current_user: UserProfile = Depends(get_current_user)) -> UserProfile:
    return current_user


@app.post("/api/v1/auth/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie("ops_session")
    return {"logged_out": True}
