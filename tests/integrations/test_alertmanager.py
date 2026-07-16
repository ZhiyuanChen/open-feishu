from __future__ import annotations

from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from feishu.gateway import GatewayConfig
from feishu.integrations.alertmanager import (
    InMemoryAlertmanagerStore,
    create_alertmanager_route,
)


def _payload(identity: str = "group_key") -> dict[str, Any]:
    payload: dict[str, Any] = {
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
    if identity != "group_key":
        payload.pop("groupKey")
    if identity == "content":
        payload["alerts"][0].pop("fingerprint")
    return payload


@pytest.mark.parametrize("identity", ("group_key", "fingerprint", "content"))
def test_webhook_updates_alert(gateway_client, identity: str) -> None:
    store = InMemoryAlertmanagerStore()
    config = GatewayConfig(app_id="cli_test", app_secret="secret", service_keys={"k-status": "status"})
    route = create_alertmanager_route(config, gateway_client, "oc_ops", store=store)
    app = Starlette(routes=[route])

    with TestClient(app) as client:
        first = client.post(
            "/alerts/alertmanager",
            headers={"Authorization": "Bearer k-status"},
            json=_payload(identity),
        )
        second = client.post(
            "/alerts/alertmanager",
            headers={"Authorization": "Bearer k-status"},
            json=_payload(identity),
        )

    assert first.status_code == 200
    assert first.json()["action"] == "created"
    assert second.status_code == 200
    assert second.json()["action"] == "updated"
    assert len(gateway_client.im.send.calls) == 1
    assert len(gateway_client.im.patch.calls) == 1


def test_webhook_shows_alert_labels(gateway_client) -> None:
    payload = _payload()
    route = create_alertmanager_route(
        GatewayConfig(app_id="cli_test", app_secret="secret", service_keys={"k-status": "status"}),
        gateway_client,
        "oc_ops",
    )

    with TestClient(Starlette(routes=[route])) as client:
        response = client.post(
            "/alerts/alertmanager",
            headers={"Authorization": "Bearer k-status"},
            json=payload,
        )

    assert response.status_code == 200
    _, card = gateway_client.im.send.calls[0][0]
    body = card["body"]["elements"][0]["content"]
    assert "ClusterGPUNodeUnhealthy" in body
    assert payload["groupKey"] not in body


def test_webhook_requires_auth(gateway_client) -> None:
    config = GatewayConfig(app_id="cli_test", app_secret="secret", service_keys={"k-status": "status"})
    app = Starlette(routes=[create_alertmanager_route(config, gateway_client, "oc_ops")])

    with TestClient(app) as client:
        resp = client.post("/alerts/alertmanager", json=_payload())

    assert resp.status_code == 401
    assert gateway_client.im.send.calls == []


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
