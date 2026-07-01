import pytest

from tests.conftest import envelope, paginated_responder

TASK = {"guid": "d116", "task_id": "t100041", "summary": "写周报", "status": "todo"}


@pytest.fixture
async def tasks(client_factory, recorder):
    """task.tasks namespace returning a single TASK envelope; records every request."""
    client = client_factory(recorder=recorder, responder=lambda r: envelope({"task": TASK}))
    try:
        yield client.task.tasks
    finally:
        await client.aclose()


class TestCreate:
    async def test_create_returns_task(self, tasks, recorder):
        resp = await tasks.create({"summary": "写周报"})
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/task/v2/tasks")
        assert body["summary"] == "写周报"
        assert resp["task"]["guid"] == "d116"


class TestGet:
    async def test_get_by_guid(self, tasks, recorder):
        resp = await tasks.get("d116")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("/task/v2/tasks/d116")
        assert resp["task"]["task_id"] == "t100041"


class TestPatch:
    async def test_patch_wraps_task_and_fields(self, tasks, recorder):
        await tasks.update("d116", {"summary": "写月报"}, ["summary"])
        method, path, _, body = recorder.last
        assert method == "PATCH" and path.endswith("/task/v2/tasks/d116")
        # Feishu's whitelist-update semantics: task carries new values, update_fields names them.
        assert body == {"task": {"summary": "写月报"}, "update_fields": ["summary"]}

    async def test_patch_materializes_fields_iterable(self, tasks, recorder):
        await tasks.update("d116", {"summary": "x"}, (f for f in ["summary", "due"]))
        assert recorder.last[3]["update_fields"] == ["summary", "due"]


class TestDelete:
    async def test_delete(self, tasks, recorder):
        await tasks.delete("d116")
        method, path, _, _ = recorder.last
        assert method == "DELETE" and path.endswith("/task/v2/tasks/d116")


class TestList:
    async def test_paginates(self, client_factory, recorder):
        responder = paginated_responder([[{"guid": "d1"}], [{"guid": "d2"}]])
        client = client_factory(recorder=recorder, responder=responder)
        result = await client.as_user("u-tok").task.tasks.list()
        assert [t["guid"] for t in result] == ["d1", "d2"]
        method, path, _, _ = recorder[0]
        assert method == "GET" and path.endswith("/task/v2/tasks")
        assert recorder[1][2]["page_token"] == "p2"
        await client.aclose()

    async def test_forwards_completed_and_caps_page_size(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.as_user("u-tok").task.tasks.list(completed=False, page_size=999)
        _, _, params, _ = recorder[0]
        assert params["completed"] == "false"
        assert int(params["page_size"]) <= 50
        await client.aclose()

    async def test_omits_completed_when_unset(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.as_user("u-tok").task.tasks.list()
        assert "completed" not in recorder[0][2]
        await client.aclose()


class TestUserIdType:
    """user_id_type forwards as a query param across every method that accepts it."""

    @pytest.mark.parametrize(
        "call",
        [
            lambda ns: ns.create({"summary": "x"}, user_id_type="open_id"),
            lambda ns: ns.get("d116", user_id_type="open_id"),
            lambda ns: ns.update("d116", {"summary": "x"}, ["summary"], user_id_type="open_id"),
        ],
        ids=["create", "get", "patch"],
    )
    async def test_forwards(self, tasks, recorder, call):
        await call(tasks)
        assert recorder.last[2]["user_id_type"] == "open_id"

    async def test_list_forwards(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.as_user("u-tok").task.tasks.list(user_id_type="open_id")
        assert recorder[0][2]["user_id_type"] == "open_id"
        await client.aclose()


class TestUserScopeRouting:
    async def test_list_routes_user_token(self, client_factory, recorder):
        # task.tasks.list is a user-token-only endpoint; the call must carry the user token.
        record = []

        def handler(request):
            record.append(request.headers.get("Authorization"))
            return envelope({"items": [], "has_more": False})

        client = client_factory(recorder=recorder, responder=handler)
        await client.as_user("u-task").task.tasks.list()
        assert record[-1] == "Bearer u-task"
        await client.aclose()
