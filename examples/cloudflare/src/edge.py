"""Lightweight Worker-edge handling for Feishu URL verification."""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit


async def inspect_url_verification_request(
    request: Any, verification_token: str | None
) -> tuple[int, dict[str, Any]] | None:
    """Handle only the Feishu URL-verification handshake before Durable Object startup."""
    method = getattr(request.method, "value", request.method)
    if str(method).upper() != "POST" or urlsplit(str(request.url)).path != "/feishu/event":
        return None

    try:
        payload = await request.clone().json()
    except Exception:
        return None
    if not isinstance(payload, Mapping) or payload.get("type") != "url_verification":
        return None

    if verification_token is not None and not hmac.compare_digest(str(payload.get("token", "")), verification_token):
        return 401, {"msg": "token mismatch"}
    return 200, {"challenge": payload.get("challenge")}
