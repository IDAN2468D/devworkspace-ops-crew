import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.config import get_settings
from app.db import UserModel, get_session
from app.schemas import AuthTokenResponse, UserProfile

settings = get_settings()
router = APIRouter(prefix="/api/v1/auth", tags=["Google Auth & MCP"])
bearer_scheme = HTTPBearer(auto_error=False)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

# Scopes needed for Sign-In + Gmail/Drive MCP read + HITL-approved draft/move
GOOGLE_OAUTH_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

# In-memory CSRF state store (swap to Redis in multi-replica production)
_oauth_states: set[str] = set()


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
    """Persist OAuth credentials JSON so Google MCP servers (Gmail/Drive) can authenticate."""
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
) -> UserProfile:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token. Sign in via /api/v1/auth/google/login first.",
        )
    try:
        decoded = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        user_id = decoded.get("sub")
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired JWT token",
        ) from exc

    with get_session() as session:
        user = session.get(UserModel, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        return _to_user_profile(user)


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserProfile | None:
    """Enforces auth when AUTH_REQUIRED=true; allows anonymous local testing otherwise."""
    if not settings.auth_required and not credentials:
        return None
    return get_current_user(credentials)


@router.get("/google/login")
def google_login() -> dict[str, str]:
    """Generate a Google OAuth 2.0 URL for Sign-In / Registration + Google MCP scopes."""
    if not settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GOOGLE_CLIENT_ID is not configured in .env",
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
    return {
        "auth_url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}",
        "state": state,
    }


@router.get("/google/callback", response_model=AuthTokenResponse)
async def google_callback(
    response: Response,
    code: str = Query(...),
    state: str = Query(...),
) -> AuthTokenResponse:
    """Handle Google OAuth callback: register or log in user, save Google MCP tokens, issue JWT."""
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
    expires_in = int(token_data.get("expires_in", 3600))
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    is_new_user = False
    with get_session() as session:
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


@router.get("/me", response_model=UserProfile)
def get_me(current_user: UserProfile = Depends(get_current_user)) -> UserProfile:
    """Return the signed-in Google user profile and Google MCP connection status."""
    return current_user


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie("ops_session")
    return {"logged_out": True}
