import pytest

from tests.conftest import envelope

RESERVE = {
    "id": "765",
    "meeting_no": "121065965",
    "url": "https://vc.feishu.cn/j/121065965",
    "app_link": "https://applink.feishu.cn/client/videochat/open?id=765",
    "live_link": "https://meetings.feishu.cn/s/abc",
    "end_time": "1700003600",
}


@pytest.fixture
async def reserves(client_factory, recorder):
    client = client_factory(recorder=recorder, responder=lambda r: envelope({"reserve": RESERVE}))
    try:
        yield client.vc.reserves
    finally:
        await client.aclose()


class TestApply:
    async def test_apply_returns_reserve(self, reserves, recorder):
        resp = await reserves.apply({"topic": "周会"})
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/vc/v1/reserves/apply")
        assert body["meeting_settings"] == {"topic": "周会"}
        assert resp["reserve"]["meeting_no"] == "121065965"

    async def test_forwards_optional_fields(self, reserves, recorder):
        await reserves.apply({"topic": "x"}, end_time="1700003600", owner_id="ou_1", user_id_type="open_id")
        _, _, params, body = recorder.last
        assert body["end_time"] == "1700003600"
        assert body["owner_id"] == "ou_1"
        assert params["user_id_type"] == "open_id"

    async def test_omits_unset_optionals(self, reserves, recorder):
        await reserves.apply({"topic": "x"})
        _, _, params, body = recorder.last
        assert "end_time" not in body and "owner_id" not in body
        assert "user_id_type" not in params


class TestGet:
    async def test_get_by_id(self, reserves, recorder):
        resp = await reserves.get("765")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("/vc/v1/reserves/765")
        assert resp["reserve"]["id"] == "765"

    async def test_forwards_user_id_type(self, reserves, recorder):
        await reserves.get("765", user_id_type="union_id")
        assert recorder.last[2]["user_id_type"] == "union_id"


class TestUpdate:
    async def test_puts_only_set_fields(self, reserves, recorder):
        await reserves.update("765", end_time="1700003600")
        method, path, _, body = recorder.last
        assert method == "PUT" and path.endswith("/vc/v1/reserves/765")
        assert body == {"end_time": "1700003600"}

    async def test_forwards_settings(self, reserves, recorder):
        await reserves.update("765", meeting_settings={"topic": "新主题"}, user_id_type="user_id")
        _, _, params, body = recorder.last
        assert body["meeting_settings"] == {"topic": "新主题"}
        assert params["user_id_type"] == "user_id"


class TestDelete:
    async def test_delete_by_id(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({}))
        await client.vc.reserves.delete("765")
        method, path, _, _ = recorder.last
        assert method == "DELETE" and path.endswith("/vc/v1/reserves/765")
        await client.aclose()


class TestUserScopeRouting:
    async def test_routes_user_token_via_as_user(self, client_factory, recorder):
        # vc scopes are user-scoped: as_user(...).vc must carry the user token, not the tenant token.
        record = []

        def handler(request):
            record.append(request.headers.get("Authorization"))
            return envelope({"reserve": RESERVE})

        client = client_factory(recorder=recorder, responder=handler)
        await client.as_user("u-token").vc.reserves.apply({"topic": "x"})
        assert record[-1] == "Bearer u-token"
        await client.aclose()
