from __future__ import annotations

from chanfig import NestedDict
from starlette.testclient import TestClient

from feishu.gateway.server import create_app_from_env, run_gateway


class _Recorder:
    def __init__(self, ret):
        self.ret = ret
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.ret


class _StubIM:
    def __init__(self):
        self.send = _Recorder(NestedDict({"message_id": "om_gateway"}))


class _StubClient:
    def __init__(self):
        self.im = _StubIM()


def _env() -> dict[str, str]:
    return {
        "FEISHU_APP_ID": "cli_test",
        "FEISHU_APP_SECRET": "secret",
        "FEISHU_GATEWAY_SERVICE_KEYS": "status:k-status",
        "FEISHU_GATEWAY_HOST": "0.0.0.0",
        "FEISHU_GATEWAY_PORT": "8123",
    }


def test_create_app_from_env_builds_a_service_authenticated_gateway() -> None:
    stub = _StubClient()
    app = create_app_from_env(_env(), client=stub)

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        resp = client.post(
            "/messages/send",
            headers={"Authorization": "Bearer k-status"},
            json={"receive_id": "oc_ops", "content": "hello"},
        )

    assert resp.status_code == 200
    assert stub.im.send.calls == [(("oc_ops", "hello"), {})]


def test_run_gateway_uses_configured_host_port_and_injected_runner() -> None:
    calls = []

    def runner(app, *, host: str, port: int) -> None:
        calls.append((app, host, port))

    run_gateway(_env(), client=_StubClient(), runner=runner)

    assert len(calls) == 1
    assert calls[0][1:] == ("0.0.0.0", 8123)
