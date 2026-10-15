from __future__ import annotations

from importlib import import_module

from starlette.testclient import TestClient

from tests.conftest import encrypt_event, signed_event

try:
    create_app = import_module("examples.cloudflare.src.agent_app").create_app
except (ImportError, AttributeError):
    create_app = None


def test_worker_asgi_app_reports_readiness_and_echoes_feishu_challenge() -> None:
    assert callable(create_app), "Cloudflare ASGI test app is unavailable"

    with TestClient(create_app()) as client:
        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json() == {"ready": True, "status": "ready"}

        response = client.post(
            "/feishu/event",
            json={"type": "url_verification", "challenge": "cf-probe", "token": "cf-smoke"},
        )
        assert response.status_code == 200
        assert response.json() == {"challenge": "cf-probe"}


def test_worker_asgi_app_decrypts_signed_feishu_challenge() -> None:
    encrypted_body = {
        "encrypt": encrypt_event(
            "cf-encrypt",
            {"type": "url_verification", "challenge": "cf-encrypted", "token": "cf-smoke"},
        )
    }
    raw_body, headers = signed_event(encrypted_body, encrypt_key="cf-encrypt")

    with TestClient(create_app()) as client:
        response = client.post("/feishu/event", content=raw_body, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"challenge": "cf-encrypted"}
