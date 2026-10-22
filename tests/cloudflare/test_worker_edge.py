from __future__ import annotations

import base64
import json
from importlib import import_module
from typing import Any

import pytest

from tests.conftest import encrypt_event, signed_event

inspect_url_verification_request = import_module("examples.cloudflare.src.edge").inspect_url_verification_request


class _RequestBody:
    def __init__(self, payload: Any, raw_body: bytes | None = None) -> None:
        self.payload = payload
        self.raw_body = raw_body if raw_body is not None else json.dumps(payload).encode()

    async def json(self) -> Any:
        return self.payload

    async def text(self) -> str:
        return self.raw_body.decode()


class _Request:
    method = "POST"
    url = "https://worker.example/feishu/event"

    def __init__(
        self,
        payload: Any,
        *,
        path: str = "/feishu/event",
        raw_body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.raw_body = raw_body if raw_body is not None else json.dumps(payload).encode()
        self.headers = headers or {}
        self.url = f"https://worker.example{path}"
        self.clone_calls = 0

    def clone(self) -> _RequestBody:
        self.clone_calls += 1
        return _RequestBody(self.payload, self.raw_body)


async def test_worker_edge_answers_authenticated_challenge_without_consuming_request() -> None:
    request = _Request({"type": "url_verification", "token": "test-token", "challenge": "abc"})

    result = await inspect_url_verification_request(request, "test-token")

    assert result == (200, {"challenge": "abc"})
    assert request.clone_calls == 1


async def test_worker_edge_rejects_challenge_with_wrong_token() -> None:
    request = _Request({"type": "url_verification", "token": "wrong", "challenge": "abc"})

    result = await inspect_url_verification_request(request, "test-token")

    assert result == (401, {"msg": "token mismatch"})


async def test_worker_edge_passes_normal_event_through_without_changing_request() -> None:
    request = _Request({"schema": "2.0", "header": {"event_type": "im.message.receive_v1"}})

    result = await inspect_url_verification_request(request, "test-token")

    assert result is None
    assert request.clone_calls == 1


async def test_worker_edge_skips_body_parsing_for_other_paths() -> None:
    request = _Request({"type": "url_verification", "token": "test-token"}, path="/ready")

    result = await inspect_url_verification_request(request, "test-token")

    assert result is None
    assert request.clone_calls == 0


async def test_worker_edge_answers_signed_encrypted_challenge_before_durable_object() -> None:
    encrypt_key = "test-encrypt-key"
    verification_token = "test-token"
    payload = {
        "encrypt": encrypt_event(
            encrypt_key,
            {"type": "url_verification", "token": verification_token, "challenge": "encrypted-abc"},
        )
    }
    raw_body, headers = signed_event(payload, encrypt_key=encrypt_key)
    request = _Request(payload, raw_body=raw_body, headers=headers)

    result = await inspect_url_verification_request(request, verification_token, encrypt_key)

    assert result == (200, {"challenge": "encrypted-abc"})
    assert request.clone_calls == 1


async def test_worker_edge_rejects_encrypted_challenge_with_invalid_signature() -> None:
    encrypt_key = "test-encrypt-key"
    payload = {
        "encrypt": encrypt_event(
            encrypt_key,
            {"type": "url_verification", "token": "test-token", "challenge": "encrypted-abc"},
        )
    }
    raw_body, headers = signed_event(payload, encrypt_key=encrypt_key)
    headers["X-Lark-Signature"] = "invalid"
    request = _Request(payload, raw_body=raw_body, headers=headers)

    result = await inspect_url_verification_request(request, "test-token", encrypt_key)

    assert result == (401, {"msg": "signature mismatch"})


async def test_worker_edge_answers_unsigned_encrypted_challenge_with_verified_token() -> None:
    payload = {
        "encrypt": encrypt_event(
            "test-encrypt-key",
            {"type": "url_verification", "token": "test-token", "challenge": "unsigned-abc"},
        )
    }
    request = _Request(payload)

    result = await inspect_url_verification_request(request, "test-token", "test-encrypt-key")

    assert result == (200, {"challenge": "unsigned-abc"})
    assert request.clone_calls == 1


@pytest.mark.parametrize("verification_token", [None, ""])
async def test_worker_edge_unsigned_encrypted_challenge_requires_configured_token(verification_token) -> None:
    payload = {
        "encrypt": encrypt_event(
            "test-encrypt-key", {"type": "url_verification", "token": "", "challenge": "unsigned-abc"}
        )
    }

    result = await inspect_url_verification_request(_Request(payload), verification_token, "test-encrypt-key")

    assert result == (401, {"msg": "signature mismatch"})


@pytest.mark.parametrize(
    "plaintext",
    [
        {"type": "event_callback", "token": "test-token"},
        {"schema": "2.0", "header": {"event_type": "im.message.receive_v1", "token": "test-token"}},
        {"type": "url_verification", "token": "wrong", "challenge": "abc"},
        {"type": "url_verification", "challenge": "abc"},
        {"type": "url_verification", "token": None, "challenge": "abc"},
        {"type": "url_verification", "token": 123, "challenge": "abc"},
        {"type": "url_verification", "token": "错误", "challenge": "abc"},
        {"type": "url_verification", "token": "test-token"},
        {"type": "url_verification", "token": "test-token", "challenge": None},
        [],
    ],
)
async def test_worker_edge_unsigned_encrypted_nonchallenge_or_wrong_token_has_uniform_rejection(plaintext: Any) -> None:
    payload = {"encrypt": encrypt_event("test-encrypt-key", plaintext)}

    result = await inspect_url_verification_request(_Request(payload), "test-token", "test-encrypt-key")

    assert result == (401, {"msg": "signature mismatch"})


@pytest.mark.parametrize("ciphertext", [None, 123, [], "", "not base64", "YQ=="])
async def test_worker_edge_unsigned_invalid_ciphertext_has_uniform_rejection(ciphertext: Any) -> None:
    result = await inspect_url_verification_request(_Request({"encrypt": ciphertext}), "test-token", "test-encrypt-key")

    assert result == (401, {"msg": "signature mismatch"})


@pytest.mark.parametrize("iv_byte", [0, 15], ids=["invalid-json", "invalid-padding"])
async def test_worker_edge_unsigned_decryption_or_json_failure_has_uniform_rejection(iv_byte) -> None:
    # Alter the CBC IV: byte 0 corrupts JSON while preserving padding; byte 15 corrupts only padding.
    blob = bytearray(base64.b64decode(encrypt_event("test-encrypt-key", {})))
    blob[iv_byte] ^= 1
    ciphertext = base64.b64encode(blob).decode("ascii")

    result = await inspect_url_verification_request(_Request({"encrypt": ciphertext}), "test-token", "test-encrypt-key")

    assert result == (401, {"msg": "signature mismatch"})


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Lark-Signature": ""},
        {"X-Lark-Signature": "invalid"},
        {"X-Lark-Request-Timestamp": "1"},
        {"X-Lark-Request-Nonce": "nonce"},
        {"X-Lark-Request-Timestamp": "1", "X-Lark-Request-Nonce": "nonce"},
    ],
)
async def test_worker_edge_does_not_treat_incomplete_signature_headers_as_unsigned(headers) -> None:
    payload = {
        "encrypt": encrypt_event(
            "test-encrypt-key", {"type": "url_verification", "token": "test-token", "challenge": "abc"}
        )
    }

    result = await inspect_url_verification_request(
        _Request(payload, headers=headers), "test-token", "test-encrypt-key"
    )

    assert result == (401, {"msg": "signature mismatch"})


@pytest.mark.parametrize("valid_signature", [False, True])
async def test_worker_edge_encrypted_normal_event_requires_valid_mac(valid_signature) -> None:
    payload = {
        "encrypt": encrypt_event(
            "test-encrypt-key",
            {"schema": "2.0", "header": {"event_type": "im.message.receive_v1", "token": "test-token"}},
        )
    }
    raw_body, headers = signed_event(payload, encrypt_key="test-encrypt-key")
    if not valid_signature:
        headers["X-Lark-Signature"] = "invalid"

    result = await inspect_url_verification_request(
        _Request(payload, raw_body=raw_body, headers=headers), "test-token", "test-encrypt-key"
    )

    assert result == (None if valid_signature else (401, {"msg": "signature mismatch"}))
