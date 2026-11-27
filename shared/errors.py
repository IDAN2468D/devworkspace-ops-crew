from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def register_error_handlers(app: FastAPI, service_name: str) -> None:
    """Centralized error manager returning uniform JSON errors across all microservices."""

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.status_code,
                    "detail": exc.detail,
                    "service": service_name,
                    "path": request.url.path,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                "detail": exc.detail,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": 422,
                    "detail": exc.errors(),
                    "service": service_name,
                    "path": request.url.path,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                "detail": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": 500,
                    "detail": str(exc),
                    "service": service_name,
                    "path": request.url.path,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                "detail": "Internal server error",
            },
        )
