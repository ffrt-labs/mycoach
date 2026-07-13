"""Shared FastAPI dependencies."""

import hmac

from fastapi import Header, HTTPException, status

from mycoach.config import get_settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Guard an endpoint with a shared API key (``X-API-Key`` header).

    Compares the header against ``settings.api_token`` in constant time. Used by
    the universal workout push endpoint so external clients (the offline logger,
    scripts, iOS Shortcuts) can authenticate without a full session. If no token
    is configured server-side the endpoint is treated as disabled (401).
    """
    token = get_settings().api_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API token not configured. Set MYCOACH_API_TOKEN to enable this endpoint.",
        )
    if not x_api_key or not hmac.compare_digest(x_api_key, token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key.",
        )
