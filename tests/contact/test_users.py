import pytest

from feishu.contact import normalize_user
from tests.conftest import envelope, paginated_responder


@pytest.fixture
async def users(client_factory, recorder):
    """Bound users namespace over a recorder; default responder returns an empty envelope."""

    clients = []

    def factory(responder=lambda r: envelope({})):
        client = client_factory(recorder=recorder, responder=responder)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()


class TestGetUser:
    async def test_get_returns_raw_user(self, users, recorder):
        user = {"user_id": "u1", "enterprise_email": "bob@ent.com", "status": {"is_activated": True}}
        client = users(lambda r: envelope({"user": user}))
        result = await client.contact.users.get("u1")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("contact/v3/users/u1")
        # Raw Feishu body passes through untouched; normalization is opt-in by the caller.
        assert result["user"]["enterprise_email"] == "bob@ent.com"
        normalized = normalize_user(result["user"])
        assert normalized["email"] == "bob@ent.com" and normalized["active"] is True


class TestListUsers:
    async def test_list_paginates(self, users, recorder):
        responder = paginated_responder([[{"user_id": "u1"}], [{"user_id": "u2"}]])
        client = users(responder)
        result = await client.contact.users.list(department_id="od-1")
        assert [u["user_id"] for u in result] == ["u1", "u2"]
        method, path, _, _ = recorder[0]
        assert method == "GET" and path.endswith("contact/v3/users/find_by_department")


class TestSearchUsers:
    async def test_search_paginates(self, users, recorder):
        responder = paginated_responder([[{"open_id": "ou_1"}], [{"open_id": "ou_2"}]], data_key="users")
        client = users(responder)
        result = await client.as_user("u-tok").contact.users.search("Bob")
        assert [u["open_id"] for u in result] == ["ou_1", "ou_2"]
        method, path, params, _ = recorder[0]
        assert method == "GET" and path.endswith("search/v1/user")
        assert params["query"] == "Bob"
        # The page_token from page 1 is forwarded on the second request.
        assert recorder[1][2]["page_token"] == "p2"

    async def test_search_caps_page_size_and_max_items(self, users, recorder):
        responder = paginated_responder(
            [[{"open_id": "ou_1"}, {"open_id": "ou_2"}], [{"open_id": "ou_3"}]], data_key="users"
        )
        client = users(responder)
        result = await client.as_user("u-tok").contact.users.search("x", page_size=999, max_items=1)
        assert [u["open_id"] for u in result] == ["ou_1"]
        assert int(recorder[0][2]["page_size"]) <= 50
        assert len(recorder) == 1

    async def test_search_routes_user_token(self, users):
        # contact:user:search is user-token only; the call must carry the user token.
        seen = []

        def handler(request):
            seen.append(request.headers.get("Authorization"))
            return envelope({"users": [], "has_more": False})

        client = users(handler)
        await client.as_user("u-search").contact.users.search("q")
        assert seen[-1] == "Bearer u-search"


class TestBatchGetUsers:
    async def test_batch_get_returns_raw(self, users):
        two_users = [
            {"user_id": "u1", "status": {"is_activated": True}},
            {"user_id": "u2", "status": {"is_activated": False}},
        ]
        seen = {}

        def handler(request):
            seen["method"] = request.method
            seen["path"] = request.url.path
            # Each requested id reaches the wire as a repeated query key.
            seen["user_ids"] = set(request.url.params.get_list("user_ids"))
            seen["user_id_type"] = request.url.params.get("user_id_type")
            seen["department_id_type"] = request.url.params.get("department_id_type")
            return envelope({"items": two_users})

        client = users(handler)
        result = await client.contact.users.batch_get(["u1", "u2"])
        assert seen["method"] == "GET" and seen["path"].endswith("/contact/v3/users/batch")
        assert seen["user_ids"] == {"u1", "u2"}
        # Project-wide default id types when unset.
        assert seen["user_id_type"] == "open_id"
        assert seen["department_id_type"] == "open_department_id"
        # Raw items surface directly; normalization derives the active flag on demand.
        assert [u["user_id"] for u in result] == ["u1", "u2"]
        assert normalize_user(result[0])["active"] is True
        assert normalize_user(result[1])["active"] is False

    async def test_batch_get_forwards_id_types(self, users, recorder):
        client = users(lambda r: envelope({"items": []}))
        await client.contact.users.batch_get(["ou_1"], user_id_type="open_id", department_id_type="department_id")
        _, _, params, _ = recorder.last
        assert params["user_id_type"] == "open_id"
        assert params["department_id_type"] == "department_id"

    async def test_batch_get_rejects_over_50(self, users, recorder):
        client = users(lambda r: envelope({"items": []}))
        with pytest.raises(ValueError):
            await client.contact.users.batch_get([f"u{i}" for i in range(51)])
        # The cap is enforced before any request is issued.
        assert len(recorder) == 0


class TestBatchGetUserIds:
    async def test_batch_get_id_posts_body(self, users, recorder):
        client = users(lambda r: envelope({"user_list": []}))
        result = await client.contact.users.batch_get_id(
            emails=["alice@example.com"], mobiles=["+8613800000000"], include_resigned=True
        )
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/contact/v3/users/batch_get_id")
        assert "alice@example.com" in body["emails"]
        assert "+8613800000000" in body["mobiles"]
        assert body["include_resigned"] is True
        assert result == {"user_list": []}


class TestWriteUsers:
    """create / update / delete share the same id-type query-param contract."""

    async def test_create_posts_body(self, users, recorder):
        client = users(lambda r: envelope({"user": {"user_id": "u1"}}))
        result = await client.contact.users.create({"name": "Bob", "department_ids": ["0"]})
        method, path, params, body = recorder.last
        assert method == "POST" and path.endswith("/contact/v3/users")
        assert body["name"] == "Bob" and body["department_ids"] == ["0"]
        # Optional id-type query params are omitted when unset (one representative case).
        assert "user_id_type" not in params and "department_id_type" not in params
        assert result["user"]["user_id"] == "u1"

    async def test_update_patches_body(self, users, recorder):
        client = users(lambda r: envelope({"user": {"name": "Bobby"}}))
        result = await client.contact.users.update("u1", {"name": "Bobby"})
        method, path, _, body = recorder.last
        assert method == "PATCH" and path.endswith("/contact/v3/users/u1")
        assert body["name"] == "Bobby"
        assert result["user"]["name"] == "Bobby"

    async def test_delete_issues_delete(self, users, recorder):
        client = users(lambda r: envelope({}))
        await client.contact.users.delete("u1")
        method, path, _, _ = recorder.last
        assert method == "DELETE" and path.endswith("/contact/v3/users/u1")

    @pytest.mark.parametrize(
        "call",
        [
            lambda ns: ns.create({"name": "Bob"}, user_id_type="open_id", department_id_type="department_id"),
            lambda ns: ns.update("ou_1", {"name": "Bobby"}, user_id_type="open_id", department_id_type="department_id"),
            lambda ns: ns.delete("ou_1", user_id_type="open_id"),
        ],
        ids=["create", "update", "delete"],
    )
    async def test_write_forwards_id_types(self, users, recorder, call):
        client = users(lambda r: envelope({"user": {}}))
        await call(client.contact.users)
        _, _, params, _ = recorder.last
        assert params["user_id_type"] == "open_id"
