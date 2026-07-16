from __future__ import annotations

from chanfig import NestedDict
from starlette.applications import Starlette
from starlette.testclient import TestClient

from feishu.gateway import GatewayConfig
from feishu.integrations.alertmanager import (
    InMemoryAlertmanagerStore,
    alertmanager_event_id,
    create_alertmanager_route,
)


class _Recorder:
    def __init__(self, ret):
        self.ret = ret
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.ret


class _StubIM:
    def __init__(self):
        self.send = _Recorder(NestedDict({"message_id": "om_alert"}))
        self.patch = _Recorder(NestedDict({"message_id": "om_alert"}))


class _StubClient:
    def __init__(self):
        self.im = _StubIM()


def _payload() -> dict:
    return {
        "receiver": "cluster-oncall",
        "status": "firing",
        "groupKey": '{}:{alertname="ClusterGPUNodeUnhealthy", cluster="a800-1"}',
        "externalURL": "https://status.example.test",
        "commonLabels": {
            "alertname": "ClusterGPUNodeUnhealthy",
            "cluster": "a800-1",
            "severity": "critical",
        },
        "commonAnnotations": {"summary": "GPU health failed"},
        "alerts": [
            {
                "status": "firing",
                "fingerprint": "fp-1",
                "labels": {"node": "compute-0022"},
                "annotations": {"description": "NVML check failed"},
                "generatorURL": "https://status.example.test/alerting/list",
            }
        ],
    }


def test_event_id_prefers_group_key() -> None:
    assert alertmanager_event_id(_payload()).startswith("{}:")


def test_event_id_falls_back_to_alert_fingerprint() -> None:
    payload = _payload()
    payload.pop("groupKey")

    assert alertmanager_event_id(payload) == "fp-1"


def test_event_id_stably_hashes_payload_without_group_key_or_fingerprint() -> None:
    payload = _payload()
    payload.pop("groupKey")
    payload["alerts"][0].pop("fingerprint")

    assert alertmanager_event_id(payload).startswith("sha256:")
    assert alertmanager_event_id(payload) == alertmanager_event_id(payload)


def test_alertmanager_route_creates_then_updates_same_event() -> None:
    stub = _StubClient()
    store = InMemoryAlertmanagerStore()
    config = GatewayConfig(app_id="cli_test", app_secret="secret", service_keys={"k-status": "status"})
    route = create_alertmanager_route(config, stub, "oc_ops", store=store)
    app = Starlette(routes=[route])

    with TestClient(app) as client:
        first = client.post(
            "/alerts/alertmanager",
            headers={"Authorization": "Bearer k-status"},
            json=_payload(),
        )
        second = client.post(
            "/alerts/alertmanager",
            headers={"Authorization": "Bearer k-status"},
            json=_payload(),
        )

    assert first.status_code == 200
    assert first.json()["action"] == "created"
    assert second.status_code == 200
    assert second.json()["action"] == "updated"
    assert len(stub.im.send.calls) == 1
    assert len(stub.im.patch.calls) == 1
    _, send_kwargs = stub.im.send.calls[0]
    assert send_kwargs["receive_id_type"] == "chat_id"
    assert send_kwargs["msg_type"] == "interactive"
    assert send_kwargs["uuid"].startswith("am-")


def test_alertmanager_route_requires_service_auth() -> None:
    stub = _StubClient()
    config = GatewayConfig(app_id="cli_test", app_secret="secret", service_keys={"k-status": "status"})
    app = Starlette(routes=[create_alertmanager_route(config, stub, "oc_ops")])

    with TestClient(app) as client:
        resp = client.post("/alerts/alertmanager", json=_payload())

    assert resp.status_code == 401
    assert stub.im.send.calls == []


def test_webhook_requires_alertmanager_capability(gateway_client) -> None:
    config = GatewayConfig(
        app_id="cli_test",
        app_secret="secret",
        service_keys={"k-status": "status", "k-messages": "messaging"},
        service_capabilities={
            "status": frozenset({"/alerts/alertmanager"}),
            "messaging": frozenset({"/messages/*"}),
        },
    )
    app = Starlette(routes=[create_alertmanager_route(config, gateway_client, "oc_ops")])

    with TestClient(app) as client:
        denied = client.post(
            "/alerts/alertmanager",
            headers={"Authorization": "Bearer k-messages"},
            json=_payload(),
        )
        allowed = client.post(
            "/alerts/alertmanager",
            headers={"Authorization": "Bearer k-status"},
            json=_payload(),
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
