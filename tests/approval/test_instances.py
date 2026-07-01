import pytest

from tests.conftest import envelope, make_client, paginated_responder


@pytest.fixture
async def instances(recorder):
    client = make_client(recorder=recorder, responder=lambda r: envelope({}))
    try:
        yield client.approval.instances
    finally:
        await client.aclose()


class TestCreate:
    async def test_create_returns_instance(self, recorder):
        client = make_client(recorder=recorder, responder=lambda r: envelope({"instance_code": "INST1"}))
        instance = {"approval_code": "ABC123", "user_id": "u1", "form": "[]"}
        resp = await client.approval.instances.create(instance)
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/approval/v4/instances")
        assert body == instance
        assert resp["instance_code"] == "INST1"
        await client.aclose()


class TestGet:
    async def test_get_returns_data(self, recorder):
        client = make_client(recorder=recorder, responder=lambda r: envelope({"approval_code": "ABC123"}))
        resp = await client.approval.instances.get("INST1")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("/approval/v4/instances/INST1")
        assert resp["approval_code"] == "ABC123"
        await client.aclose()


def _list_responder(pages):
    return paginated_responder(pages, data_key="instance_code_list")


class TestList:
    async def test_list_paginates(self, recorder):
        client = make_client(recorder=recorder, responder=_list_responder([["INST1"], ["INST2"]]))
        items = await client.approval.instances.list("ABC123", "100", "200")
        assert items == ["INST1", "INST2"]
        method, path, params, _ = recorder[0]
        assert method == "GET" and path.endswith("/approval/v4/instances")
        assert (params["approval_code"], params["start_time"], params["end_time"]) == ("ABC123", "100", "200")
        assert recorder[1][2]["page_token"] == "p2"
        await client.aclose()

    async def test_caps_page_size(self, recorder):
        client = make_client(recorder=recorder, responder=_list_responder([[]]))
        await client.approval.instances.list("ABC123", "100", "200", page_size=999)
        assert int(recorder[0][2]["page_size"]) <= 50
        await client.aclose()

    async def test_honors_max_items(self, recorder):
        client = make_client(recorder=recorder, responder=_list_responder([["INST1", "INST2"], ["INST3"]]))
        items = await client.approval.instances.list("ABC123", "100", "200", max_items=1)
        assert items == ["INST1"]
        assert len(recorder) == 1
        await client.aclose()


class TestCancel:
    async def test_cancel_posts_body(self, instances, recorder):
        await instances.cancel("ABC123", "INST1", "u1")
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/approval/v4/instances/cancel")
        assert body == {"approval_code": "ABC123", "instance_code": "INST1", "user_id": "u1"}

    async def test_cancel_honors_user_id_type(self, instances, recorder):
        await instances.cancel("ABC123", "INST1", "ou_1", user_id_type="open_id")
        method, path, params, body = recorder.last
        assert method == "POST" and path.endswith("/approval/v4/instances/cancel")
        assert params["user_id_type"] == "open_id"
        assert body == {"approval_code": "ABC123", "instance_code": "INST1", "user_id": "ou_1"}
