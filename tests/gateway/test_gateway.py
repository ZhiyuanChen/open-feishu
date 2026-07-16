import pytest
from chanfig import NestedDict
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from feishu.errors import FeishuApiError
from feishu.gateway import GatewayConfig, GatewayContext, create_gateway
from feishu.gateway.config import parse_service_keys
from feishu.gateway.errors import feishu_error_response


class _Recorder:
    def __init__(self, ret):
        self.ret = ret
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.ret


class StubIM:
    def __init__(self):
        self.send = _Recorder(NestedDict({"message_id": "om_stub", "msg_type": "text"}))


class StubUsers:
    def __init__(self):
        self.get = _Recorder(NestedDict({"user": {"open_id": "ou_1", "name": "Ann"}}))
        self.list = _Recorder([NestedDict({"open_id": "ou_1"})])
        self.batch_get_id = _Recorder(NestedDict({"user_list": [{"email": "ann@example.com", "open_id": "ou_1"}]}))


class StubDepartments:
    def __init__(self):
        self.get = _Recorder(NestedDict({"department": {"open_department_id": "od_1", "name": "Eng"}}))
        self.list = _Recorder([NestedDict({"open_department_id": "od_1"})])


class StubContact:
    def __init__(self):
        self.users = StubUsers()
        self.departments = StubDepartments()


class StubClient:
    def __init__(self):
        self.im = StubIM()
        self.contact = StubContact()
        self.closed = False

    async def aclose(self):
        self.closed = True


def _app():
    stub = StubClient()
    config = GatewayConfig(app_id="cli_test", app_secret="secret", service_keys={"k-status": "status"})
    return TestClient(create_gateway(config, client=stub)), stub


def _auth():
    return {"Authorization": "Bearer k-status"}


class _StatusIntegration:
    def routes(self, context: GatewayContext):
        async def status(_):
            return JSONResponse({"app_id": context.config.app_id})

        return [Route("/integrations/status", status, methods=["GET"])]


class TestGatewayAuth:
    def test_env_service_keys_are_key_to_service_mapping(self):
        config = GatewayConfig.from_env(
            {
                "FEISHU_APP_ID": "cli_test",
                "FEISHU_APP_SECRET": "secret",
                "FEISHU_GATEWAY_SERVICE_KEYS": "status:k-status,tracker:k-tracker",
            }
        )

        assert config.service_keys == {"k-status": "status", "k-tracker": "tracker"}
        assert parse_service_keys("status:k-status") == {"k-status": "status"}

    def test_requires_at_least_one_service_key(self):
        config = GatewayConfig(app_id="cli_test", app_secret="secret")

        try:
            create_gateway(config, client=StubClient())
        except ValueError as exc:
            assert "service API key" in str(exc)
        else:
            raise AssertionError("create_gateway should reject an empty service_keys map")

    def test_health_and_ready_do_not_require_service_key(self):
        client, _ = _app()
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ok"}

    def test_mounts_integration_routes(self):
        config = GatewayConfig(app_id="cli_test", app_secret="secret", service_keys={"k-status": "status"})

        with TestClient(create_gateway(config, client=StubClient(), integrations=(_StatusIntegration(),))) as client:
            response = client.get("/integrations/status")

        assert response.json() == {"app_id": "cli_test"}

    def test_internal_routes_require_service_key(self):
        client, _ = _app()

        assert client.post("/messages/send", json={"receive_id": "ou_1", "content": "hi"}).status_code == 401
        assert (
            client.post(
                "/messages/send",
                headers={"Authorization": "Bearer wrong"},
                json={"receive_id": "ou_1", "content": "hi"},
            ).status_code
            == 401
        )

    def test_service_capabilities_limit_routes(self):
        stub = StubClient()
        config = GatewayConfig(
            app_id="cli_test",
            app_secret="secret",
            service_keys={"k-org": "org-sync", "k-messages": "messaging"},
            service_capabilities={
                "org-sync": frozenset({"/org/*"}),
                "messaging": frozenset({"/messages/send"}),
            },
        )

        with TestClient(create_gateway(config, client=stub)) as client:
            denied_message = client.post(
                "/messages/send",
                headers={"Authorization": "Bearer k-org"},
                json={"receive_id": "oc_ops", "content": "blocked"},
            )
            denied_card = client.post(
                "/messages/card",
                headers={"Authorization": "Bearer k-messages"},
                json={"receive_id": "oc_ops", "card": {"schema": "2.0"}},
            )
            message = client.post(
                "/messages/send",
                headers={"Authorization": "Bearer k-messages"},
                json={"receive_id": "oc_ops", "content": "allowed"},
            )
            org = client.get("/org/users", headers={"Authorization": "Bearer k-org"})

        assert denied_message.status_code == 403
        assert denied_card.status_code == 403
        assert message.status_code == 200
        assert org.status_code == 200
        assert stub.im.send.calls == [(("oc_ops", "allowed"), {})]

    @pytest.mark.parametrize(
        ("service_keys", "service_capabilities", "message"),
        (
            (
                {"k-status": "status", "k-messages": "messaging"},
                {"status": frozenset({"/alerts/alertmanager"})},
                "missing capability entries",
            ),
            (
                {"k-status": "status"},
                {"unknown": frozenset({"/alerts/alertmanager"})},
                "unknown services",
            ),
        ),
    )
    def test_rejects_incomplete_capability_config(self, service_keys, service_capabilities, message):
        with pytest.raises(ValueError, match=message):
            GatewayConfig(
                app_id="cli_test",
                app_secret="secret",
                service_keys=service_keys,
                service_capabilities=service_capabilities,
            )

    @pytest.mark.parametrize("capability", ("/org/*/*", "/org/users*"))
    def test_rejects_invalid_capability_wildcards(self, capability):
        with pytest.raises(ValueError, match="wildcards must be terminal"):
            GatewayConfig(
                app_id="cli_test",
                app_secret="secret",
                service_keys={"k-org": "org"},
                service_capabilities={"org": frozenset({capability})},
            )


class TestGatewayErrors:
    def test_business_api_errors_are_caller_faults(self):
        resp = feishu_error_response(FeishuApiError(230002, "denied"))

        assert resp.status_code == 400


class TestMessages:
    def test_send_delegates_to_im_send(self):
        client, stub = _app()

        resp = client.post(
            "/messages/send",
            headers=_auth(),
            json={
                "receive_id": "ou_1",
                "receive_id_type": "open_id",
                "content": "hello",
                "msg_type": "text",
                "uuid": "dedupe-1",
            },
        )

        assert resp.status_code == 200
        assert resp.json()["message_id"] == "om_stub"
        assert stub.im.send.calls == [
            (
                ("ou_1", "hello"),
                {"receive_id_type": "open_id", "msg_type": "text", "uuid": "dedupe-1"},
            )
        ]

    def test_card_route_sends_interactive_message(self):
        client, stub = _app()
        card = {"schema": "2.0", "body": {"elements": []}}

        resp = client.post(
            "/messages/card",
            headers=_auth(),
            json={"receive_id": "oc_1", "card": card, "receive_id_type": "chat_id", "uuid": "card-1"},
        )

        assert resp.status_code == 200
        assert stub.im.send.calls == [
            (
                ("oc_1", card),
                {"receive_id_type": "chat_id", "msg_type": "interactive", "uuid": "card-1"},
            )
        ]


class TestOrgRoutes:
    def test_lists_users_as_org_facts(self):
        client, stub = _app()

        resp = client.get(
            "/org/users",
            headers=_auth(),
            params={
                "department_id": "od_root",
                "user_id_type": "user_id",
                "department_id_type": "department_id",
                "page_size": "20",
                "max_items": "10",
            },
        )

        assert resp.status_code == 200
        assert resp.json() == [{"open_id": "ou_1"}]
        assert stub.contact.users.list.calls == [
            (
                ("od_root",),
                {
                    "user_id_type": "user_id",
                    "department_id_type": "department_id",
                    "page_size": 20,
                    "max_items": 10,
                },
            )
        ]

    def test_gets_departments_as_org_facts(self):
        client, stub = _app()

        resp = client.get(
            "/org/departments/od_1",
            headers=_auth(),
            params={"department_id_type": "department_id"},
        )

        assert resp.status_code == 200
        assert resp.json()["department"]["name"] == "Eng"
        assert stub.contact.departments.get.calls == [(("od_1",), {"department_id_type": "department_id"})]

    def test_resolves_user_ids(self):
        client, stub = _app()

        resp = client.post(
            "/org/resolve",
            headers=_auth(),
            json={"emails": ["ann@example.com"], "mobiles": ["+8613800000000"], "include_resigned": True},
        )

        assert resp.status_code == 200
        assert resp.json()["user_list"][0]["open_id"] == "ou_1"
        assert stub.contact.users.batch_get_id.calls == [
            (
                (),
                {
                    "emails": ["ann@example.com"],
                    "mobiles": ["+8613800000000"],
                    "include_resigned": True,
                },
            )
        ]
