from __future__ import annotations

from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from feishu.gateway import GatewayConfig
from feishu.integrations.alertmanager import (
    InMemoryAlertmanagerStore,
    JsonFileAlertmanagerStore,
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


def _alert(
    fingerprint: str,
    node: str,
    starts_at: str,
    *,
    status: str = "firing",
) -> dict[str, Any]:
    return {
        "status": status,
        "fingerprint": fingerprint,
        "startsAt": starts_at,
        "labels": {"node": node},
        "annotations": {"description": f"{node} failed"},
        "generatorURL": "https://status.example.test/alerting/list",
    }


def _group(status: str, *alerts: dict[str, Any]) -> dict[str, Any]:
    payload = _payload()
    payload["status"] = status
    payload["alerts"] = list(alerts)
    return payload


def _post_alerts(route, *payloads: dict[str, Any]) -> list[str]:
    headers = {"Authorization": "Bearer k-status"}
    with TestClient(Starlette(routes=[route])) as client:
        responses = [
            client.post(
                "/alerts/alertmanager",
                headers=headers,
                json=payload,
            )
            for payload in payloads
        ]
    assert all(response.status_code == 200 for response in responses)
    return [response.json()["action"] for response in responses]


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


def test_refiring_alert_creates_a_new_message(gateway_client) -> None:
    store = InMemoryAlertmanagerStore()
    config = GatewayConfig(app_id="cli_test", app_secret="secret", service_keys={"k-status": "status"})
    route = create_alertmanager_route(config, gateway_client, "oc_ops", store=store)
    first_start = "2026-07-17T02:40:41Z"
    second_start = "2026-07-21T04:32:27Z"

    actions = _post_alerts(
        route,
        _group("firing", _alert("fp-link", "compute-0015", first_start)),
        _group("resolved", _alert("fp-link", "compute-0015", first_start, status="resolved")),
        _group("firing", _alert("fp-link", "compute-0015", second_start)),
        _group("firing", _alert("fp-link", "compute-0015", second_start)),
    )

    assert actions == ["created", "updated", "created", "updated"]
    assert len(gateway_client.im.send.calls) == 2
    assert len(gateway_client.im.patch.calls) == 2


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


def test_webhook_uses_supplied_card_builder(gateway_client) -> None:
    seen: list[dict[str, Any]] = []

    def card_builder(payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(payload)
        return {
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "Custom"}},
            "body": {"elements": [{"tag": "markdown", "content": "custom-card"}]},
        }

    route = create_alertmanager_route(
        GatewayConfig(app_id="cli_test", app_secret="secret", service_keys={"k-status": "status"}),
        gateway_client,
        "oc_ops",
        card_builder=card_builder,
    )

    assert _post_alerts(route, _payload()) == ["created"]
    assert seen == [_payload()]
    _, card = gateway_client.im.send.calls[0][0]
    assert card["body"]["elements"][0]["content"] == "custom-card"


def test_single_alert_alias_updates_when_group_key_gains_node(gateway_client) -> None:
    config = GatewayConfig(app_id="cli_test", app_secret="secret", service_keys={"k-status": "status"})
    route = create_alertmanager_route(config, gateway_client, "oc_ops", store=InMemoryAlertmanagerStore())
    base = _payload()
    base["commonLabels"]["job"] = "cluster-health-a800-1"
    base["alerts"][0]["labels"] = {"node": "compute-0015"}
    base["alerts"][0]["startsAt"] = "2026-07-17T02:40:41Z"
    base["commonLabels"]["alertname"] = "ClusterMMHealthNetworkEntityFailed"
    base["groupKey"] = (
        '{}:{alertname="ClusterMMHealthNetworkEntityFailed", cluster="a800-1", job="cluster-health-a800-1"}'
    )

    with_node = dict(base)
    with_node["status"] = "resolved"
    with_node["groupKey"] = (
        '{}:{alertname="ClusterMMHealthNetworkEntityFailed", cluster="a800-1", '
        'job="cluster-health-a800-1", node="compute-0015"}'
    )
    with_node["alerts"] = [{**base["alerts"][0], "status": "resolved"}]

    assert _post_alerts(route, base, with_node) == ["created", "updated"]
    assert len(gateway_client.im.send.calls) == 1
    assert len(gateway_client.im.patch.calls) == 1


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


def test_group_status_does_not_regress(gateway_client, tmp_path) -> None:
    config = GatewayConfig(
        app_id="cli_test",
        app_secret="secret",
        service_keys={"k-status": "status"},
    )
    store_path = tmp_path / "messages.json"
    stale_start = "2026-07-17T07:00:00Z"
    older = "2026-07-17T08:00:00Z"
    newer = "2026-07-17T09:00:00Z"

    route = create_alertmanager_route(
        config,
        gateway_client,
        "oc_ops",
        store=JsonFileAlertmanagerStore(store_path),
    )
    actions = _post_alerts(
        route,
        _group(
            "firing",
            _alert("fp-old", "node-old", older),
            _alert("fp-new", "node-new", newer),
        ),
        _group(
            "resolved",
            _alert(
                "fp-new",
                "node-new",
                stale_start,
                status="resolved",
            ),
        ),
    )

    route = create_alertmanager_route(
        config,
        gateway_client,
        "oc_ops",
        store=JsonFileAlertmanagerStore(store_path),
    )
    actions += _post_alerts(
        route,
        _group(
            "firing",
            _alert("fp-old", "node-old", older),
            _alert("fp-new", "node-new", newer, status="resolved"),
        ),
        _group(
            "resolved",
            _alert("fp-old", "node-old", older, status="resolved"),
        ),
    )

    assert actions == ["created", "ignored_stale", "updated", "updated"]
    assert len(gateway_client.im.patch.calls) == 2
    _, final_card = gateway_client.im.patch.calls[-1][0]
    assert final_card["header"]["template"] == "green"
