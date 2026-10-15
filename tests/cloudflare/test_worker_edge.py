from __future__ import annotations

from importlib import import_module
from typing import Any

inspect_url_verification_request = import_module("examples.cloudflare.src.edge").inspect_url_verification_request


class _RequestBody:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    async def json(self) -> Any:
        return self.payload


class _Request:
    method = "POST"
    url = "https://worker.example/feishu/event"

    def __init__(self, payload: Any, *, path: str = "/feishu/event") -> None:
        self.payload = payload
        self.url = f"https://worker.example{path}"
        self.clone_calls = 0

    def clone(self) -> _RequestBody:
        self.clone_calls += 1
        return _RequestBody(self.payload)


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
