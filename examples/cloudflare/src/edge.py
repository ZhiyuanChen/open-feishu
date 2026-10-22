"""Lightweight Worker-edge handling for Feishu URL verification."""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from feishu.errors import FeishuCryptoError
from feishu.events.crypto import decrypt
from feishu.signature import SignatureVerifier


async def inspect_url_verification_request(
    request: Any, verification_token: str | None, encrypt_key: str | None = None
) -> tuple[int, dict[str, Any]] | None:
    """Handle a Feishu URL-verification handshake before Durable Object startup."""
    method = getattr(request.method, "value", request.method)
    if str(method).upper() != "POST" or urlsplit(str(request.url)).path != "/feishu/event":
        return None

    try:
        raw_body = (await request.clone().text()).encode("utf-8")
        payload = json.loads(raw_body)
    except Exception:
        return None
    if not isinstance(payload, Mapping):
        return None

    if payload.get("type") == "url_verification":
        return _challenge_response(payload, verification_token)

    if "encrypt" not in payload:
        return None
    unauthorized = (401, {"msg": "signature mismatch"})
    signature_headers = {
        name: request.headers.get(name)
        for name in ("X-Lark-Request-Timestamp", "X-Lark-Request-Nonce", "X-Lark-Signature")
    }
    has_signature = any(value is not None for value in signature_headers.values())
    if encrypt_key is None:
        return (400, {"msg": "encrypted body but no encrypt_key"}) if has_signature else unauthorized
    if has_signature:
        if not SignatureVerifier(encrypt_key).is_valid_request(raw_body, signature_headers):
            return unauthorized
    elif not verification_token:
        return unauthorized

    ciphertext = payload.get("encrypt")
    if not isinstance(ciphertext, str):
        return (400, {"msg": "invalid encrypted body"}) if has_signature else unauthorized
    try:
        decrypted = json.loads(decrypt(encrypt_key, ciphertext))
    except (FeishuCryptoError, ValueError, TypeError):
        return (400, {"msg": "invalid encrypted body"}) if has_signature else unauthorized
    if not isinstance(decrypted, Mapping):
        return (400, {"msg": "invalid event payload"}) if has_signature else unauthorized
    if decrypted.get("type") != "url_verification":
        return None if has_signature else unauthorized

    if has_signature:
        return _challenge_response(decrypted, verification_token)

    # Setup challenges can omit signatures; only a decrypted, token-authenticated handshake is exempt.
    token = decrypted.get("token")
    challenge = decrypted.get("challenge")
    if not verification_token or not isinstance(token, str) or not isinstance(challenge, str):
        return unauthorized
    try:
        token_matches = hmac.compare_digest(token.encode("utf-8"), verification_token.encode("utf-8"))
    except UnicodeError:
        return unauthorized
    if not token_matches:
        return unauthorized
    return 200, {"challenge": challenge}


def _challenge_response(payload: Mapping[str, Any], verification_token: str | None) -> tuple[int, dict[str, Any]]:
    if verification_token is not None and not hmac.compare_digest(str(payload.get("token", "")), verification_token):
        return 401, {"msg": "token mismatch"}
    return 200, {"challenge": payload.get("challenge")}
