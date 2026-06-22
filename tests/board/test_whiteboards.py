import httpx
import pytest

from tests.conftest import envelope, make_client, token_handler


def theme_responder(request):
    return envelope({"theme": "classic"})


def nodes_responder(request):
    return envelope({"nodes": [{"id": "n1", "type": "text"}, {"id": "n2", "type": "composite_shape"}]})


class TestGetTheme:
    @pytest.fixture
    async def board(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=theme_responder)
        yield client.board
        await client.aclose()

    async def test_get_theme(self, board, recorder):
        resp = await board.get_theme("wb_abc")
        method, path, _, _ = recorder.last
        assert method == "GET"
        assert path.endswith("/board/v1/whiteboards/wb_abc/theme")
        assert resp["theme"] == "classic"


class TestListNodes:
    @pytest.fixture
    async def board(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=nodes_responder)
        yield client.board
        await client.aclose()

    async def test_returns_nodes(self, board, recorder):
        nodes = await board.list_nodes("wb_abc")
        method, path, _, _ = recorder.last
        assert method == "GET"
        assert path.endswith("/board/v1/whiteboards/wb_abc/nodes")
        # The full node list reaches the caller (this endpoint is not paginated).
        assert [n["id"] for n in nodes] == ["n1", "n2"]

    async def test_forwards_user_id_type(self, board, recorder):
        await board.list_nodes("wb_abc", user_id_type="union_id")
        assert recorder.last[2]["user_id_type"] == "union_id"

    async def test_omits_unset_user_id_type(self, board, recorder):
        await board.list_nodes("wb_abc")
        assert "user_id_type" not in recorder.last[2]

    async def test_empty_when_nodes_absent(self, client_factory):
        client = client_factory(responder=lambda r: envelope({}))
        assert await client.board.list_nodes("wb_abc") == []
        await client.aclose()


class TestDownloadAsImage:
    async def test_downloads_bytes(self):
        # The handler returns raw image bytes (not a JSON envelope) with status 200.
        seen = {}

        def handler(request):
            token = token_handler(request)
            if token is not None:
                return token
            seen["method"] = request.method
            seen["path"] = request.url.path
            return httpx.Response(200, content=b"PNG_BYTES")

        client = make_client(handler=handler)
        result = await client.board.download_as_image("wb_abc")
        assert seen["method"] == "GET"
        assert seen["path"].endswith("/board/v1/whiteboards/wb_abc/download_as_image")
        assert result == b"PNG_BYTES"
        await client.aclose()
