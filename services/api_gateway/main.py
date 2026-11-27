import re
from functools import lru_cache
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.types import ASGIApp, Receive, Scope, Send

from shared.auth_utils import decode_jwt_token
from shared.errors import register_error_handlers


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "api-gateway"
    auth_service_url: str = "http://auth-service:8001"
    hitl_service_url: str = "http://hitl-service:8002"
    rag_service_url: str = "http://rag-service:8003"
    ops_agent_service_url: str = "http://ops-agent-service:8004"
    search_service_url: str = "http://search-service:8005"

    jwt_secret_key: str = "dev-insecure-change-in-production"
    jwt_algorithm: str = "HS256"
    auth_required: bool = False


@lru_cache
def get_gateway_settings() -> GatewaySettings:
    return GatewaySettings()


settings = get_gateway_settings()

app = FastAPI(
    title="DevWorkspace Ops Crew — API Gateway",
    version="2.0.0",
    description="Unified Microservices Gateway (Auth, HITL, RAG, Search/Elasticsearch, Ops Agents + Kafka)",
)


class NormalizePathMiddleware:
    """Collapse accidental double slashes (e.g. //health when gateway_url has a trailing /)."""

    def __init__(self, asgi_app: ASGIApp) -> None:
        self.asgi_app = asgi_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and "//" in scope.get("path", ""):
            scope = dict(scope)
            scope["path"] = re.sub(r"/{2,}", "/", scope["path"])
        await self.asgi_app(scope, receive, send)


app.add_middleware(NormalizePathMiddleware)
register_error_handlers(app, settings.service_name)

ROUTE_MAP: list[tuple[str, str]] = [
    ("/api/v1/auth", settings.auth_service_url),
    ("/api/v1/hitl", settings.hitl_service_url),
    ("/api/v1/rag", settings.rag_service_url),
    ("/api/v1/ops", settings.ops_agent_service_url),
    ("/api/v1/mcp", settings.ops_agent_service_url),
    ("/api/v1/search", settings.search_service_url),
]

PUBLIC_PREFIXES = ("/health", "/api/v1/auth/google/login", "/api/v1/auth/google/callback", "/api/v1/auth/logout")


def _extract_token(request: Request) -> str | None:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return request.cookies.get("ops_session")


def _enforce_gateway_security(request: Request) -> None:
    path = request.url.path
    if any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
        return
    token = _extract_token(request)
    if not token:
        if settings.auth_required or path.startswith("/api/v1/mcp"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required. Sign in via /api/v1/auth/google/login.",
            )
        return
    decode_jwt_token(token, settings.jwt_secret_key, settings.jwt_algorithm)


def _resolve_upstream(path: str) -> str:
    for prefix, base_url in ROUTE_MAP:
        if path == prefix or path.startswith(f"{prefix}/"):
            return base_url.rstrip("/")
    raise HTTPException(status_code=404, detail=f"No upstream route for '{path}'")


@app.get("/health")
async def aggregated_health() -> dict[str, Any]:
    services_health: dict[str, Any] = {}
    targets = {
        "auth-service": settings.auth_service_url,
        "hitl-service": settings.hitl_service_url,
        "rag-service": settings.rag_service_url,
        "ops-agent-service": settings.ops_agent_service_url,
        "search-service": settings.search_service_url,
    }
    async with httpx.AsyncClient(timeout=3.0) as client:
        for name, base_url in targets.items():
            try:
                resp = await client.get(f"{base_url.rstrip('/')}/health")
                services_health[name] = resp.json() if resp.status_code == 200 else {"status": "degraded"}
            except Exception as exc:
                services_health[name] = {"status": "unreachable", "error": str(exc)}
    return {
        "service": settings.service_name,
        "status": "ok",
        "auth_required": settings.auth_required,
        "services": services_health,
    }


@app.api_route("/api/v1/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_api(full_path: str, request: Request) -> Response:
    _enforce_gateway_security(request)
    upstream_base = _resolve_upstream(request.url.path)
    upstream_url = f"{upstream_base}{request.url.path}"

    forwarded_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in {"host", "content-length"}
    }
    body = await request.body()

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            upstream_resp = await client.request(
                method=request.method,
                url=upstream_url,
                params=request.query_params,
                headers=forwarded_headers,
                content=body,
            )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Upstream service unavailable ({upstream_base}): {exc}",
            ) from exc

    excluded = {"content-encoding", "transfer-encoding", "connection", "content-length"}
    response_headers = {
        k: v for k, v in upstream_resp.headers.items() if k.lower() not in excluded
    }
    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=response_headers,
        media_type=upstream_resp.headers.get("content-type"),
    )
