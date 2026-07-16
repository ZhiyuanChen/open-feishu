from __future__ import annotations

from starlette.testclient import TestClient

from feishu.gateway import GatewayConfig, create_gateway


def test_mounts_alertmanager(gateway_client) -> None:
    from feishu.integrations import INTEGRATIONS, register_bundled_integrations

    register_bundled_integrations()
    alertmanager = INTEGRATIONS.build_gateway("alertmanager", receive_id="oc_ops")
    config = GatewayConfig(
        app_id="cli_test",
        app_secret="secret",
        service_keys={"k-status": "status"},
        service_capabilities={"status": frozenset({"/alerts/alertmanager"})},
    )

    with TestClient(create_gateway(config, client=gateway_client, integrations=(alertmanager,))) as gateway:
        response = gateway.post(
            "/alerts/alertmanager",
            headers={"Authorization": "Bearer k-status"},
            json={
                "status": "firing",
                "commonLabels": {"alertname": "ClusterGPUNodeUnhealthy"},
                "alerts": [],
            },
        )

    assert response.status_code == 200
    assert len(gateway_client.im.send.calls) == 1
