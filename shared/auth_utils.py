from typing import Any

import jwt
from fastapi import HTTPException, status


def decode_jwt_token(token: str, secret_key: str, algorithm: str = "HS256") -> dict[str, Any]:
    """Verify and decode a Bearer JWT issued by auth-service."""
    try:
        return jwt.decode(token, secret_key, algorithms=[algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired JWT token",
        ) from exc
