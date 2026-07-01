import pytest

from tests.conftest import envelope


@pytest.fixture
async def freebusy(client_factory, recorder):
    client = client_factory(recorder=recorder, responder=lambda r: envelope({"freebusy_list": []}))
    yield client.calendar.freebusy
    await client.aclose()


class TestQuery:
    async def test_query_returns_freebusy(self, freebusy, recorder):
        body = {"time_min": "t0", "time_max": "t1", "user_id": "ou_xxx"}
        resp = await freebusy.query(body)
        method, path, _, sent = recorder.last
        assert method == "POST" and path.endswith("/calendar/v4/freebusy/list")
        assert sent == body
        assert resp["freebusy_list"] == []

    async def test_forwards_user_id_type(self, freebusy, recorder):
        await freebusy.query({"time_min": "t0", "time_max": "t1", "user_id": "u_1"}, user_id_type="user_id")
        assert recorder.last[2]["user_id_type"] == "user_id"
