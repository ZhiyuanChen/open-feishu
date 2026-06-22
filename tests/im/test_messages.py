import json

import httpx
import pytest
from chanfig import NestedDict

from tests.conftest import envelope, make_client, paginated_responder, token_handler


def message_responder(request):
    return envelope({"message_id": "om_1", "body": {"content": '{"text":"hi"}'}})


@pytest.fixture
async def im(recorder):
    """Bind ``client.im`` to the recorder; configure the response per test via ``im(responder)``.

    Usage: ``ns = im(message_responder); await ns.send(...)``. Auto-closed after the test.
    """
    clients = []

    def _bind(responder=message_responder):
        c = make_client(recorder=recorder, responder=responder)
        clients.append(c)
        return c.im

    try:
        yield _bind
    finally:
        for c in clients:
            await c.aclose()


def _raw_bytes_seen(handler_body):
    """Build a (client, seen) pair whose handler captures the raw request and returns ``handler_body``.

    ``handler_body`` maps a request to an ``httpx.Response``; ``seen`` records method/path/content/body.
    """
    seen = {}

    def handler(request):
        token = token_handler(request)
        if token is not None:
            return token
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["content_type"] = request.headers.get("content-type", "")
        seen["params"] = dict(request.url.params)
        seen["body"] = request.content
        return handler_body(request)

    return make_client(handler=handler), seen


class TestSend:
    async def test_wraps_plain_string(self, im, recorder):
        # A bare string (not a JSON object) is auto-wrapped as text content,
        # and the returned message id reaches the caller.
        resp = await im().send("oc_x", "hi")
        method, path, params, body = recorder.last
        assert method == "POST" and path.endswith("/im/v1/messages")
        assert body["msg_type"] == "text"
        assert json.loads(body["content"]) == {"text": "hi"}
        assert resp["message_id"] == "om_1"

    @pytest.mark.parametrize(
        "content,kwargs,expected_msg_type",
        [
            ({"image_key": "img_v2_x"}, {}, "image"),
            ({"post": {}}, {"msg_type": "interactive"}, "interactive"),
        ],
    )
    async def test_infers_msg_type(self, im, recorder, content, kwargs, expected_msg_type):
        # msg_type is inferred from the content shape, but an explicit msg_type wins.
        await im().send("oc_x", content, **kwargs)
        assert recorder.last[3]["msg_type"] == expected_msg_type


class TestReceiveIdInference:
    @pytest.mark.parametrize(
        "receive_id,expected_type",
        [
            ("ou_xxxx", "open_id"),
            ("on_xxxx", "union_id"),
            ("oc_xxxx", "chat_id"),
            ("a@b.com", "email"),
        ],
    )
    async def test_infers_from_prefix(self, im, recorder, receive_id, expected_type):
        # Without an explicit receive_id_type, send infers it from the recipient id
        # shape and forwards it as the receive_id_type query param.
        await im(lambda r: envelope({"message_id": "om_1"})).send(receive_id, "hello")
        assert recorder.last[2]["receive_id_type"] == expected_type

    # "abcd1234" is an 8-char id: it must NOT be guessed as user_id (real Feishu
    # user_ids have no fixed length); it must surface as uninferable instead.
    @pytest.mark.parametrize("bad_id", ["garbage", "123", "abcd1234"])
    async def test_send_raises(self, im, bad_id):
        # An unrecognizable receive_id surfaces a clear ValueError at the public
        # send seam rather than silently sending a wrong type.
        with pytest.raises(ValueError, match="cannot infer receive_id_type"):
            await im().send(bad_id, "hi")

    async def test_forward_raises(self, im):
        with pytest.raises(ValueError, match="cannot infer receive_id_type"):
            await im().forward("garbage", "om_1")


class TestUploadResource:
    @pytest.mark.parametrize(
        "call,endpoint,data,key,expected_parts",
        [
            (
                lambda ns: ns.upload_image(b"PNG_BYTES"),
                "/im/v1/images",
                {"image_key": "img_v2_x"},
                "image_key",
                [(b'name="image_type"', b"message"), (b'name="image"', b"PNG_BYTES")],
            ),
            (
                lambda ns: ns.upload_file(b"hello", "a.txt"),
                "/im/v1/files",
                {"file_key": "file_v2_x"},
                "file_key",
                [
                    (b'name="file_type"', b"stream"),
                    (b'name="file_name"', b"a.txt"),
                    (b'name="file"', b"hello"),
                ],
            ),
        ],
    )
    async def test_posts_multipart(self, call, endpoint, data, key, expected_parts):
        client, seen = _raw_bytes_seen(lambda r: httpx.Response(200, json=envelope(data)))
        resp = await call(client.im)
        assert seen["method"] == "POST"
        assert seen["path"].endswith(endpoint)
        assert seen["content_type"].startswith("multipart/form-data")
        for field, value in expected_parts:
            assert field in seen["body"] and value in seen["body"]
        assert resp[key] == data[key]
        await client.aclose()


class TestForwardAndMergeForward:
    async def test_forward_infers_type(self, im, recorder):
        await im().forward("oc_target", "om_1")
        method, path, params, body = recorder.last
        assert method == "POST" and path.endswith("/im/v1/messages/om_1/forward")
        assert params["receive_id_type"] == "chat_id"
        assert body["receive_id"] == "oc_target"

    async def test_forward_explicit_type_wins(self, im, recorder):
        # receive_id looks like a chat_id, but an explicit override must win.
        await im().forward("oc_target", "om_1", receive_id_type="open_id")
        assert recorder.last[2]["receive_id_type"] == "open_id"

    async def test_merge_forward_infers_type(self, im, recorder):
        await im().merge_forward("oc_target", ["om_1", "om_2"])
        method, path, params, body = recorder.last
        assert method == "POST" and path.endswith("/im/v1/messages/merge_forward")
        assert params["receive_id_type"] == "chat_id"
        assert body["receive_id"] == "oc_target"
        assert body["message_id_list"] == ["om_1", "om_2"]
        # uuid is omitted from the body unless explicitly provided.
        assert "uuid" not in body

    async def test_merge_forward_includes_uuid(self, im, recorder):
        await im().merge_forward("ou_target", ["om_1"], uuid="dedupe-1")
        _, _, params, body = recorder.last
        assert params["receive_id_type"] == "open_id"
        assert body["uuid"] == "dedupe-1"


class TestReply:
    async def test_omits_thread_flag(self, im, recorder):
        await im().reply("om_root", "hi")
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/im/v1/messages/om_root/reply")
        # reply_in_thread is absent unless requested.
        assert "reply_in_thread" not in body

    async def test_reply_in_thread_sets_flag(self, im, recorder):
        await im().reply("om_root", "hi", reply_in_thread=True)
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/im/v1/messages/om_root/reply")
        assert body["reply_in_thread"] is True


class TestMessageLifecycle:
    async def test_update_puts_content(self, im, recorder):
        await im(lambda r: envelope({})).update("om_1", {"text": "edited"})
        method, path, _, body = recorder.last
        assert method == "PUT" and path.endswith("/im/v1/messages/om_1")
        assert body["msg_type"] == "text"
        assert json.loads(body["content"]) == {"text": "edited"}

    async def test_patch_content_only(self, im, recorder):
        card = {"elements": [{"tag": "div"}]}
        await im().patch("om_1", card)
        method, path, _, body = recorder.last
        assert method == "PATCH" and path.endswith("/im/v1/messages/om_1")
        # patch carries only the serialized content, no msg_type.
        assert "msg_type" not in body
        assert json.loads(body["content"]) == card

    async def test_recall_uses_delete(self, im, recorder):
        await im(lambda r: envelope(None)).recall("om_1")
        method, path, _, _ = recorder.last
        assert method == "DELETE" and path.endswith("/im/v1/messages/om_1")

    async def test_get_fetches_the_message(self, im, recorder):
        resp = await im().get("om_1")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("/im/v1/messages/om_1")
        assert resp["message_id"] == "om_1"


class TestListMessages:
    async def test_concatenates_pages(self, im, recorder):
        responder = paginated_responder([[{"message_id": "m1"}], [{"message_id": "m2"}]])
        items = await im(responder).list_messages("oc_chat", page_size=999)
        # Items are concatenated across both pages.
        assert [i["message_id"] for i in items] == ["m1", "m2"]
        method, path, params, _ = recorder[0]
        assert method == "GET" and path.endswith("/im/v1/messages")
        assert params["container_id"] == "oc_chat"
        assert params["container_id_type"] == "chat"
        assert params["sort_type"] == "ByCreateTimeDesc"
        # page_size is capped to the API maximum.
        assert int(params["page_size"]) <= 50
        # The token returned by page 1 is forwarded on the second request.
        assert recorder[1][2]["page_token"] == "p2"

    async def test_honors_max_items(self, im, recorder):
        responder = paginated_responder([[{"message_id": "m1"}, {"message_id": "m2"}], [{"message_id": "m3"}]])
        items = await im(responder).list_messages("oc_chat", max_items=1)
        assert [i["message_id"] for i in items] == ["m1"]
        # The walk stops once max_items is satisfied (no second page fetched).
        assert len(recorder) == 1


class TestListReplyChain:
    @staticmethod
    def _chain_client(recorder, chain):
        # chain maps message_id -> message dict (with optional parent_id). Each GET
        # im/v1/messages/{id} returns data.items=[message]; unknown ids 404 via a Feishu error code.
        def handler(request):
            token = token_handler(request)
            if token is not None:
                return token
            recorder.record(request)
            msg_id = request.url.path.rsplit("/", 1)[-1]
            message = chain.get(msg_id)
            if message is None:
                return httpx.Response(200, json=envelope(None, code=230002, msg="message not found"))
            return httpx.Response(200, json=envelope({"items": [message]}))

        return make_client(handler=handler)

    @staticmethod
    def _msg(message_id, *, parent_id=None, text="x"):
        out = {"message_id": message_id, "body": {"content": json.dumps({"text": text})}}
        if parent_id is not None:
            out["parent_id"] = parent_id
        return out

    @pytest.fixture
    async def chain_factory(self, recorder):
        clients = []

        def _build(chain):
            c = self._chain_client(recorder, chain)
            clients.append(c)
            return c.im

        try:
            yield _build
        finally:
            for c in clients:
                await c.aclose()

    async def test_oldest_first(self, chain_factory):
        ns = chain_factory(
            {
                "om_root": self._msg("om_root"),
                "om_mid": self._msg("om_mid", parent_id="om_root"),
                "om_leaf": self._msg("om_leaf", parent_id="om_mid"),
            }
        )
        items = await ns.list_reply_chain("om_leaf")
        # Walk starts at the leaf and follows parent_id up to the root.
        assert [m["message_id"] for m in items] == ["om_root", "om_mid", "om_leaf"]

    async def test_newest_first(self, chain_factory):
        ns = chain_factory(
            {
                "om_root": self._msg("om_root"),
                "om_leaf": self._msg("om_leaf", parent_id="om_root"),
            }
        )
        items = await ns.list_reply_chain("om_leaf", oldest_first=False)
        assert [m["message_id"] for m in items] == ["om_leaf", "om_root"]

    async def test_max_items_caps_the_chain(self, chain_factory, recorder):
        ns = chain_factory(
            {
                "om_root": self._msg("om_root"),
                "om_mid": self._msg("om_mid", parent_id="om_root"),
                "om_leaf": self._msg("om_leaf", parent_id="om_mid"),
            }
        )
        items = await ns.list_reply_chain("om_leaf", max_items=2)
        # Only the leaf and its immediate parent are collected; the root is not fetched.
        assert [m["message_id"] for m in items] == ["om_mid", "om_leaf"]
        assert len(recorder) == 2

    async def test_max_chars_caps_the_chain(self, chain_factory, recorder):
        ns = chain_factory(
            {
                "om_root": self._msg("om_root", text="root"),
                "om_mid": self._msg("om_mid", parent_id="om_root", text="this is a fairly long body"),
                "om_leaf": self._msg("om_leaf", parent_id="om_mid", text="this is a fairly long body"),
            }
        )
        items = await ns.list_reply_chain("om_leaf", max_chars=20)
        # The leaf body alone already exceeds max_chars, so the walk stops after one fetch.
        assert [m["message_id"] for m in items] == ["om_leaf"]
        assert len(recorder) == 1

    async def test_missing_parent_ends_chain(self, chain_factory):
        # The leaf points at a parent that 404s; the walk treats it as the chain end.
        ns = chain_factory({"om_leaf": self._msg("om_leaf", parent_id="om_gone")})
        items = await ns.list_reply_chain("om_leaf")
        assert [m["message_id"] for m in items] == ["om_leaf"]


class TestReadUsers:
    async def test_concatenates_pages(self, im, recorder):
        responder = paginated_responder([[{"user_id": "u1"}], [{"user_id": "u2"}]])
        items = await im(responder).read_users("om_1", user_id_type="union_id")
        assert [i["user_id"] for i in items] == ["u1", "u2"]
        method, path, params, _ = recorder[0]
        assert method == "GET" and path.endswith("/im/v1/messages/om_1/read_users")
        assert params["user_id_type"] == "union_id"
        assert recorder[1][2]["page_token"] == "p2"


class TestGetResource:
    @pytest.mark.parametrize(
        "kwargs,expected_type,content",
        [
            ({}, "image", b"PNG_BYTES"),
            ({"resource_type": "file"}, "file", b"FILE_BYTES"),
        ],
    )
    async def test_downloads_bytes(self, kwargs, expected_type, content):
        # get_resource returns raw bytes (not a JSON envelope) and maps resource_type
        # to the ``type`` query param.
        client, seen = _raw_bytes_seen(lambda r: httpx.Response(200, content=content))
        result = await client.im.get_resource("om_1", "file_k1", **kwargs)
        assert seen["method"] == "GET"
        assert seen["path"].endswith("/im/v1/messages/om_1/resources/file_k1")
        assert seen["params"].get("type") == expected_type
        assert result == content
        await client.aclose()


class TestPushFollowUp:
    async def test_wraps_string(self, im, recorder):
        result = await im(lambda r: envelope({})).push_follow_up("om_1", "点击查看")
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/im/v1/messages/om_1/push_follow_up")
        # A plain string is wrapped as a single-item follow_ups list.
        assert body["follow_ups"] == [{"content": "点击查看"}]
        assert isinstance(result, NestedDict)

    async def test_forwards_dict_unchanged(self, im, recorder):
        body_in = {"follow_ups": [{"content": "a"}, {"content": "b"}]}
        await im(lambda r: envelope({})).push_follow_up("om_1", body_in)
        assert recorder.last[3] == body_in
