import pytest

from tests.conftest import envelope, paginated_responder


def chat_responder(request):
    return envelope({"chat_id": "oc_test123"})


@pytest.fixture
async def chats(client_factory, recorder):
    client = client_factory(recorder=recorder, responder=chat_responder)
    yield client.im.chats
    await client.aclose()


class TestCreate:
    async def test_create_returns_chat(self, chats, recorder):
        resp = await chats.create(name="My Chat", user_id_list=["ou_abc"], chat_mode="group")
        method, path, params, body = recorder.last
        assert method == "POST" and path.endswith("/im/v1/chats")
        assert params["user_id_type"] == "open_id"
        assert body["name"] == "My Chat"
        assert body["user_id_list"] == ["ou_abc"]
        assert body["chat_mode"] == "group"
        assert resp["chat_id"] == "oc_test123"

    async def test_omits_unset_fields(self, chats, recorder):
        await chats.create(name="My Chat")
        _, _, _, body = recorder.last
        assert "user_id_list" not in body


class TestGet:
    async def test_get_returns_chat(self, chats, recorder):
        resp = await chats.get("oc_chat1")
        method, path, params, _ = recorder.last
        assert method == "GET" and path.endswith("/im/v1/chats/oc_chat1")
        assert params["user_id_type"] == "open_id"
        assert resp["chat_id"] == "oc_test123"


class TestUpdate:
    async def test_update_puts_changed_fields(self, chats, recorder):
        await chats.update("oc_chat1", name="New Name")
        method, path, _, body = recorder.last
        assert method == "PUT" and path.endswith("/im/v1/chats/oc_chat1")
        assert body["name"] == "New Name"
        # Unset fields are omitted from the update body.
        assert "description" not in body


class TestDisband:
    async def test_disband_deletes_chat(self, chats, recorder):
        await chats.disband("oc_chat1")
        method, path, _, _ = recorder.last
        assert method == "DELETE" and path.endswith("/im/v1/chats/oc_chat1")


class TestMembers:
    async def test_add_members(self, chats, recorder):
        await chats.add_members("oc_chat1", ["ou_user1", "ou_user2"])
        method, path, params, body = recorder.last
        assert method == "POST" and path.endswith("/im/v1/chats/oc_chat1/members")
        assert params["member_id_type"] == "open_id"
        assert body["id_list"] == ["ou_user1", "ou_user2"]

    async def test_remove_members(self, chats, recorder):
        await chats.remove_members("oc_chat1", ["ou_user1"])
        method, path, params, body = recorder.last
        assert method == "DELETE" and path.endswith("/im/v1/chats/oc_chat1/members")
        assert params["member_id_type"] == "open_id"
        assert body["id_list"] == ["ou_user1"]


class TestIdTypeForwarding:
    """Optional *_id_type kwargs reach the wire as the matching query param."""

    @pytest.mark.parametrize(
        "call, param",
        [
            (lambda c: c.create(name="Chat", user_id_type="union_id"), "user_id_type"),
            (lambda c: c.get("oc_chat1", user_id_type="union_id"), "user_id_type"),
            (lambda c: c.add_members("oc_chat1", ["u1"], member_id_type="union_id"), "member_id_type"),
            (lambda c: c.remove_members("oc_chat1", ["u1"], member_id_type="union_id"), "member_id_type"),
        ],
    )
    async def test_forwards_custom_id_type(self, chats, recorder, call, param):
        await call(chats)
        _, _, params, _ = recorder.last
        assert params[param] == "union_id"


class TestList:
    """list() and list_members() share the same pagination contract."""

    @pytest.fixture(params=["chats", "members"])
    def scenario(self, request):
        if request.param == "chats":
            return {
                "key": "chat_id",
                "path": "/im/v1/chats",
                "id_param": ("user_id_type", lambda c, **kw: c.list(**kw)),
            }
        return {
            "key": "member_id",
            "path": "/im/v1/chats/oc_chat1/members",
            "id_param": ("member_id_type", lambda c, **kw: c.list_members("oc_chat1", **kw)),
        }

    async def test_paginates(self, client_factory, recorder, scenario):
        key = scenario["key"]
        responder = paginated_responder([[{key: "a"}], [{key: "b"}]])
        client = client_factory(recorder=recorder, responder=responder)
        items = await scenario["id_param"][1](client.im.chats)
        assert [i[key] for i in items] == ["a", "b"]
        method, path, _, _ = recorder[0]
        assert method == "GET" and path.endswith(scenario["path"])
        # The page_token from page 1 is forwarded on the second request.
        assert recorder[1][2]["page_token"] == "p2"
        await client.aclose()

    async def test_caps_page_size(self, client_factory, recorder, scenario):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await scenario["id_param"][1](client.im.chats, page_size=999)
        _, path, params, _ = recorder[0]
        assert path.endswith(scenario["path"])
        assert int(params["page_size"]) <= 50
        await client.aclose()

    async def test_honors_max_items(self, client_factory, recorder, scenario):
        key = scenario["key"]
        responder = paginated_responder([[{key: "a"}, {key: "b"}], [{key: "c"}]])
        client = client_factory(recorder=recorder, responder=responder)
        items = await scenario["id_param"][1](client.im.chats, max_items=1)
        assert [i[key] for i in items] == ["a"]
        assert len(recorder) == 1
        await client.aclose()

    async def test_forwards_custom_id_type(self, client_factory, recorder, scenario):
        param, call = scenario["id_param"]
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await call(client.im.chats, **{param: "union_id"})
        _, _, params, _ = recorder[0]
        assert params[param] == "union_id"
        await client.aclose()
