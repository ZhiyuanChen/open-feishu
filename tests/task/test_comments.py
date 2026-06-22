import pytest

from tests.conftest import envelope, paginated_responder

COMMENT = {"id": "7654", "content": "已完成初稿", "resource_id": "d116", "resource_type": "task"}


@pytest.fixture
async def comments(client_factory, recorder):
    client = client_factory(recorder=recorder, responder=lambda r: envelope({"comment": COMMENT}))
    try:
        yield client.task.comments
    finally:
        await client.aclose()


class TestCreate:
    async def test_create_returns_comment(self, comments, recorder):
        resp = await comments.create("d116", "已完成初稿")
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/task/v2/comments")
        assert body["resource_id"] == "d116"
        assert body["content"] == "已完成初稿"
        assert body["resource_type"] == "task"  # defaults to task
        assert resp["comment"]["id"] == "7654"

    async def test_forwards_resource_type_and_user_id_type(self, comments, recorder):
        await comments.create("r1", "hi", resource_type="task", user_id_type="open_id")
        _, _, params, body = recorder.last
        assert body["resource_type"] == "task"
        assert params["user_id_type"] == "open_id"


class TestList:
    async def test_list_paginates(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[{"id": "c1"}], [{"id": "c2"}]]))
        comments = await client.task.comments.list("d116")
        assert [c["id"] for c in comments] == ["c1", "c2"]
        method, path, params, _ = recorder[0]
        assert method == "GET" and path.endswith("/task/v2/comments")
        assert params["resource_id"] == "d116"
        assert params["resource_type"] == "task"
        assert recorder[1][2]["page_token"] == "p2"
        await client.aclose()

    async def test_honors_max_items(self, client_factory, recorder):
        client = client_factory(
            recorder=recorder, responder=paginated_responder([[{"id": "c1"}, {"id": "c2"}], [{"id": "c3"}]])
        )
        comments = await client.task.comments.list("d116", max_items=1)
        assert [c["id"] for c in comments] == ["c1"]
        assert len(recorder) == 1
        await client.aclose()

    async def test_forwards_user_id_type(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.task.comments.list("d116", user_id_type="open_id")
        assert recorder[0][2]["user_id_type"] == "open_id"
        await client.aclose()
