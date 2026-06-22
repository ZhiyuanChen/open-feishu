import pytest

from feishu.im.reactions import ReactionsNamespace
from tests.conftest import envelope, paginated_responder


@pytest.fixture
async def reactions(client_factory, recorder):
    def make(responder):
        return client_factory(recorder=recorder, responder=responder)

    clients = []

    def bind(responder):
        client = make(responder)
        clients.append(client)
        return client.im.reactions

    yield bind
    for client in clients:
        await client.aclose()


class TestAccessor:
    async def test_lazily_cached(self, client):
        assert isinstance(client.im.reactions, ReactionsNamespace)
        assert client.im.reactions is client.im.reactions
        await client.aclose()


class TestCreate:
    async def test_returns_reaction(self, reactions, recorder):
        ns = reactions(lambda r: envelope({"reaction_id": "z1", "reaction_type": {"emoji_type": "THUMBSUP"}}))
        resp = await ns.create("om_1", "THUMBSUP")
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/im/v1/messages/om_1/reactions")
        assert body["reaction_type"] == {"emoji_type": "THUMBSUP"}
        assert resp["reaction_id"] == "z1"


class TestDelete:
    async def test_targets_reaction_id(self, reactions, recorder):
        ns = reactions(lambda r: envelope({"reaction_id": "z1"}))
        await ns.delete("om_1", "z1")
        method, path, _, _ = recorder.last
        assert method == "DELETE" and path.endswith("/im/v1/messages/om_1/reactions/z1")


class TestList:
    async def test_paginates(self, reactions, recorder):
        ns = reactions(paginated_responder([[{"reaction_id": "z1"}], [{"reaction_id": "z2"}]]))
        reactions_out = await ns.list("om_1")
        assert [r["reaction_id"] for r in reactions_out] == ["z1", "z2"]
        method, path, _, _ = recorder[0]
        assert method == "GET" and path.endswith("/im/v1/messages/om_1/reactions")
        assert recorder[1][2]["page_token"] == "p2"

    async def test_filters_by_emoji_type(self, reactions, recorder):
        ns = reactions(paginated_responder([[]]))
        await ns.list("om_1", emoji_type="THUMBSUP")
        assert recorder[0][2]["reaction_type"] == "THUMBSUP"

    async def test_omits_emoji_type_when_unset(self, reactions, recorder):
        ns = reactions(paginated_responder([[]]))
        await ns.list("om_1")
        assert "reaction_type" not in recorder[0][2]
