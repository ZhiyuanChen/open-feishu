import pytest

from feishu.im.pins import PinsNamespace
from tests.conftest import envelope, paginated_responder


@pytest.fixture
async def pins(client_factory, recorder):
    client = client_factory(recorder=recorder, responder=lambda r: envelope({}))
    yield client.im.pins
    await client.aclose()


class TestAccessor:
    async def test_lazy_and_cached(self, client):
        assert isinstance(client.im.pins, PinsNamespace)
        assert client.im.pins is client.im.pins
        await client.aclose()


class TestCreate:
    async def test_create_returns_pin(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"pin": {"message_id": "om_1"}}))
        resp = await client.im.pins.create("om_1")
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/im/v1/pins")
        assert body["message_id"] == "om_1"
        assert resp["pin"]["message_id"] == "om_1"
        await client.aclose()


class TestDelete:
    async def test_delete_targets_message(self, pins, recorder):
        await pins.delete("om_1")
        method, path, _, _ = recorder.last
        assert method == "DELETE" and path.endswith("/im/v1/pins/om_1")


class TestList:
    async def test_paginates(self, client_factory, recorder):
        responder = paginated_responder([[{"message_id": "om_1"}], [{"message_id": "om_2"}]])
        client = client_factory(recorder=recorder, responder=responder)
        pins = await client.im.pins.list("oc_1")
        assert [p["message_id"] for p in pins] == ["om_1", "om_2"]
        method, path, params, _ = recorder[0]
        assert method == "GET" and path.endswith("/im/v1/pins")
        assert params["chat_id"] == "oc_1"
        assert recorder[1][2]["page_token"] == "p2"
        await client.aclose()

    async def test_forwards_time_window(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.im.pins.list("oc_1", start_time="1700000000000", end_time="1700003600000")
        _, _, params, _ = recorder[0]
        assert params["start_time"] == "1700000000000"
        assert params["end_time"] == "1700003600000"
        await client.aclose()

    async def test_omits_time_window_when_unset(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.im.pins.list("oc_1")
        _, _, params, _ = recorder[0]
        assert "start_time" not in params and "end_time" not in params
        await client.aclose()
