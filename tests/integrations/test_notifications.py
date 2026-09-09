from __future__ import annotations

from starlette.applications import Starlette
from starlette.testclient import TestClient

from feishu.agent.summarization import TextSummaryRequest
from feishu.gateway import GatewayConfig
from feishu.gateway.notifications import InMemoryEventMessageStore
from feishu.integrations.notifications import create_notification_route, notification_summary


def _payload() -> dict:
    return {
        "event_id": "tracker:run-1",
        "source": "tracker",
        "title": "训练流程完成",
        "status": "completed",
        "target": {"receive_id": "oc_ops", "receive_id_type": "chat_id"},
        "summary_input": "run-1 completed after 22 minutes. accuracy improved to 0.91.",
        "links": [{"title": "Open tracker", "url": "https://tracker.example/runs/run-1"}],
    }


def test_notification_route_summarizes_and_upserts_card(gateway_client) -> None:
    seen: list[TextSummaryRequest] = []

    async def summarizer(request: TextSummaryRequest) -> str:
        seen.append(request)
        return "训练流程已完成，accuracy 到 0.91。"

    route = create_notification_route(
        GatewayConfig(app_id="cli_test", app_secret="secret", service_keys={"k-tracker": "tracker"}),
        gateway_client,
        text_summarizer=summarizer,
        store=InMemoryEventMessageStore(),
    )

    with TestClient(Starlette(routes=[route])) as client:
        first = client.post("/notifications", headers={"Authorization": "Bearer k-tracker"}, json=_payload())
        second = client.post("/notifications", headers={"Authorization": "Bearer k-tracker"}, json=_payload())

    assert first.status_code == 200
    assert first.json()["action"] == "created"
    assert second.status_code == 200
    assert second.json()["action"] == "updated"
    assert [request.kind for request in seen] == ["workflow_notification", "workflow_notification"]
    assert len(gateway_client.im.send.calls) == 1
    assert len(gateway_client.im.patch.calls) == 1
    _, card = gateway_client.im.send.calls[0][0]
    body = "\n".join(element["content"] for element in card["body"]["elements"] if element["tag"] == "markdown")
    assert "训练流程已完成" in body
    assert "[Open tracker](https://tracker.example/runs/run-1)" in body


def test_notification_route_requires_explicit_target(gateway_client) -> None:
    route = create_notification_route(
        GatewayConfig(app_id="cli_test", app_secret="secret", service_keys={"k-status": "status"}),
        gateway_client,
    )
    payload = _payload()
    payload.pop("target")

    with TestClient(Starlette(routes=[route])) as client:
        response = client.post("/notifications", headers={"Authorization": "Bearer k-status"}, json=payload)

    assert response.status_code == 400
    assert response.json()["msg"] == "notification payload requires target.receive_id"
    assert gateway_client.im.send.calls == []


def test_notification_route_requires_capability(gateway_client) -> None:
    route = create_notification_route(
        GatewayConfig(
            app_id="cli_test",
            app_secret="secret",
            service_keys={"k-tracker": "tracker"},
            service_capabilities={"tracker": frozenset({"/other"})},
        ),
        gateway_client,
    )

    with TestClient(Starlette(routes=[route])) as client:
        response = client.post("/notifications", headers={"Authorization": "Bearer k-tracker"}, json=_payload())

    assert response.status_code == 403
    assert gateway_client.im.send.calls == []


async def test_notification_summary_falls_back_to_payload_text() -> None:
    summary = await notification_summary(
        {"summary_input": "a" * 20},
        text_summarizer=None,
        max_chars=10,
    )

    assert summary == "aaaaaaaaa…"
