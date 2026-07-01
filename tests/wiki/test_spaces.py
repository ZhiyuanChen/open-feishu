import pytest

from tests.conftest import envelope, paginated_responder


@pytest.fixture
async def wiki(recorder):
    """Build a wiki namespace bound to ``recorder`` with a chosen responder, auto-closed."""
    clients = []

    def build(responder):
        from tests.conftest import make_client

        client = make_client(recorder=recorder, responder=responder)
        clients.append(client)
        return client.wiki

    yield build
    for client in clients:
        await client.aclose()


def node_responder(request):
    return envelope({"node": {"node_token": "wikcnnew", "obj_type": "docx", "title": "New Doc"}})


class TestListSpaces:
    async def test_paginates(self, wiki, recorder):
        responder = paginated_responder([[{"space_id": "7001"}], [{"space_id": "7002"}]])
        items = await wiki(responder).list_spaces()
        assert [i["space_id"] for i in items] == ["7001", "7002"]
        method, path, _, _ = recorder[0]
        assert method == "GET" and path.endswith("/wiki/v2/spaces")
        assert recorder[1][2]["page_token"] == "p2"

    async def test_caps_page_size(self, wiki, recorder):
        await wiki(paginated_responder([[]])).list_spaces(page_size=999)
        assert int(recorder[0][2]["page_size"]) <= 50

    async def test_honors_max_items(self, wiki, recorder):
        responder = paginated_responder([[{"space_id": "7001"}, {"space_id": "7002"}], [{"space_id": "7003"}]])
        items = await wiki(responder).list_spaces(max_items=1)
        assert [i["space_id"] for i in items] == ["7001"]
        assert len(recorder) == 1


class TestGetSpace:
    async def test_returns_space(self, wiki, recorder):
        resp = await wiki(lambda r: envelope({"space": {"space_id": "7001"}})).get_space("7001")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("/wiki/v2/spaces/7001")
        assert resp["space"]["space_id"] == "7001"


class TestGetNode:
    async def test_returns_node(self, wiki, recorder):
        resp = await wiki(lambda r: envelope({"node": {"node_token": "wikcn1"}})).get_node("doccnxxx")
        method, path, params, _ = recorder.last
        assert method == "GET" and path.endswith("/wiki/v2/spaces/get_node")
        assert params["token"] == "doccnxxx"
        assert "obj_type" not in params  # omitted when not provided
        assert resp["node"]["node_token"] == "wikcn1"

    async def test_forwards_obj_type(self, wiki, recorder):
        await wiki(lambda r: envelope({"node": {}})).get_node("doccnxxx", obj_type="docx")
        assert recorder.last[2]["obj_type"] == "docx"


class TestListNodes:
    async def test_paginates(self, wiki, recorder):
        responder = paginated_responder([[{"node_token": "wikcn1"}], [{"node_token": "wikcn2"}]])
        items = await wiki(responder).list_nodes("7001")
        assert [i["node_token"] for i in items] == ["wikcn1", "wikcn2"]
        method, path, _, _ = recorder[0]
        assert method == "GET" and path.endswith("/wiki/v2/spaces/7001/nodes")
        assert recorder[1][2]["page_token"] == "p2"

    async def test_parent_node_token(self, wiki, recorder):
        # Absence-when-unset, then forwarded when set.
        await wiki(paginated_responder([[]])).list_nodes("7001")
        assert "parent_node_token" not in recorder[0][2]
        await wiki(paginated_responder([[]])).list_nodes("7001", parent_node_token="wikcn0")
        assert recorder.last[2]["parent_node_token"] == "wikcn0"

    async def test_caps_page_size(self, wiki, recorder):
        await wiki(paginated_responder([[]])).list_nodes("7001", page_size=999)
        assert int(recorder[0][2]["page_size"]) <= 50

    async def test_honors_max_items(self, wiki, recorder):
        responder = paginated_responder([[{"node_token": "wikcn1"}, {"node_token": "wikcn2"}], [{"node_token": "n3"}]])
        items = await wiki(responder).list_nodes("7001", max_items=1)
        assert [i["node_token"] for i in items] == ["wikcn1"]
        assert len(recorder) == 1


class TestCreateNode:
    async def test_returns_node(self, wiki, recorder):
        resp = await wiki(node_responder).create_node("7001", "docx", title="New Doc")
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/wiki/v2/spaces/7001/nodes")
        assert body["obj_type"] == "docx"
        assert body["title"] == "New Doc"
        assert resp["node"]["node_token"] == "wikcnnew"

    async def test_omits_unset_fields(self, wiki, recorder):
        await wiki(node_responder).create_node("7001", "docx")
        # Absence-when-unset is the behavior under test.
        assert recorder.last[3]["obj_type"] == "docx"
        assert "title" not in recorder.last[3]

    async def test_forwards_parent_node_token(self, wiki, recorder):
        await wiki(node_responder).create_node("7001", "docx", parent_node_token="wikcn0")
        assert recorder.last[3]["parent_node_token"] == "wikcn0"


class TestMoveNode:
    async def test_posts_target_parent(self, wiki, recorder):
        resp = await wiki(lambda r: envelope({"node": {"node_token": "wikcn1"}})).move_node("7001", "wikcn1", "wikcn0")
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/wiki/v2/spaces/7001/nodes/wikcn1/move")
        assert body["target_parent_token"] == "wikcn0"
        assert resp["node"]["node_token"] == "wikcn1"


class TestUpdateNodeTitle:
    async def test_posts_title(self, wiki, recorder):
        await wiki(lambda r: envelope({})).update_node_title("7001", "wikcn1", "New Title")
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/wiki/v2/spaces/7001/nodes/wikcn1/update_title")
        assert body["title"] == "New Title"


class TestSearch:
    async def test_paginates(self, wiki, recorder):
        responder = paginated_responder([[{"node_id": "n1"}], [{"node_id": "n2"}]])
        nodes = await wiki(responder).search("test")
        assert [n["node_id"] for n in nodes] == ["n1", "n2"]
        method, path, params, body = recorder[0]
        assert method == "POST" and path.endswith("/wiki/v1/nodes/search")
        assert body["query"] == "test"
        assert int(params["page_size"]) <= 50
        # Second page carries the page_token; query body is unchanged.
        assert recorder[1][3]["query"] == "test"
        assert recorder[1][2]["page_token"] == "p2"

    async def test_scopes_to_space_id(self, wiki, recorder):
        await wiki(paginated_responder([[]])).search("q", space_id="7001")
        body = recorder.last[3]
        assert body["query"] == "q"
        assert body["space_id"] == "7001"

    async def test_caps_page_size(self, wiki, recorder):
        await wiki(paginated_responder([[]])).search("q", page_size=999)
        assert int(recorder[0][2]["page_size"]) <= 50
