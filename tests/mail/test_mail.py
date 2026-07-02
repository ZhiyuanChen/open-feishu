import httpx
import pytest

from tests.conftest import envelope, paginated_responder


@pytest.fixture
async def mail(client_factory, recorder):
    clients = []

    def factory(responder=lambda r: envelope({}), *, handler=None):
        kwargs = {"handler": handler} if handler is not None else {"responder": responder}
        client = client_factory(recorder=recorder, **kwargs)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()


class TestMailUsers:
    async def test_query_posts_email_list(self, mail, recorder):
        client = mail(lambda r: envelope({"user_list": [{"email": "alice@example.com", "status": 4, "type": 1}]}))

        result = await client.mail.users.query(["alice@example.com"])

        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/mail/v1/users/query")
        assert body == {"email_list": ["alice@example.com"]}
        assert result["user_list"][0]["email"] == "alice@example.com"


class TestMailMessages:
    async def test_list_paginates_mailbox_messages(self, mail, recorder):
        client = mail(paginated_responder([["m1"], ["m2"]]))

        result = await client.mail.messages.list(
            "user@example.com", page_size=999, folder_id="INBOX", only_unread=True, label_id="FLAGGED"
        )

        assert result == ["m1", "m2"]
        method, path, params, _ = recorder[0]
        assert method == "GET" and path.endswith("/mail/v1/user_mailboxes/user@example.com/messages")
        assert params["page_size"] == "20"
        assert params["folder_id"] == "INBOX"
        assert params["only_unread"] == "true"
        assert params["label_id"] == "FLAGGED"
        assert recorder[1][2]["page_token"] == "p2"

    async def test_get_forwards_format_and_quotes_message_id_path_segment(self, client_factory):
        seen = {}

        def handler(request):
            seen["raw_path"] = request.url.raw_path
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json=envelope({"message": {"message_id": "m/1", "subject": "Hello"}}))

        client = client_factory(handler=handler)

        try:
            result = await client.mail.messages.get("me", "m/1", format="metadata")
        finally:
            await client.aclose()

        assert seen["raw_path"].endswith(b"/mail/v1/user_mailboxes/me/messages/m%2F1?format=metadata")
        assert seen["params"]["format"] == "metadata"
        assert result["message"]["subject"] == "Hello"

    async def test_send_posts_sparse_body_with_user_token(self, mail, recorder):
        seen = {}

        def handler(request):
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=envelope({"message_id": "msg_1", "thread_id": "thread_1"}))

        client = mail(handler=handler)

        result = await client.as_user("u-mail").mail.messages.send(
            "me",
            subject="Hello",
            to=[{"mail_address": "alice@example.com", "name": "Alice"}],
            body_plain_text="Hi",
            dedupe_key="dedupe-1",
            head_from={"name": "Ops"},
        )

        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/mail/v1/user_mailboxes/me/messages/send")
        assert seen["auth"] == "Bearer u-mail"
        assert body == {
            "subject": "Hello",
            "to": [{"mail_address": "alice@example.com", "name": "Alice"}],
            "body_plain_text": "Hi",
            "dedupe_key": "dedupe-1",
            "head_from": {"name": "Ops"},
        }
        assert result["message_id"] == "msg_1"

    async def test_search_posts_body_paginates_and_uses_user_token(self, mail, recorder):
        seen_auth = []

        def handler(request):
            seen_auth.append(request.headers.get("authorization"))
            page_token = request.url.params.get("page_token")
            data = (
                {"items": [{"id": "m1"}], "has_more": True, "page_token": "p2"}
                if page_token is None
                else {"items": [{"id": "m2"}, {"id": "m3"}], "has_more": False}
            )
            return httpx.Response(200, json=envelope(data))

        client = mail(handler=handler)

        result = await client.as_user("u-mail").mail.messages.search(
            "me",
            query="合同审批通知",
            filter={"from": ["alice@example.com"], "is_unread": True},
            page_size=999,
            max_items=2,
        )

        assert [item["id"] for item in result] == ["m1", "m2"]
        method, path, params, body = recorder[0]
        assert method == "POST" and path.endswith("/mail/v1/user_mailboxes/me/search")
        assert params["page_size"] == "15"
        assert "page_token" not in params
        assert body == {"query": "合同审批通知", "filter": {"from": ["alice@example.com"], "is_unread": True}}
        assert recorder[1][2]["page_token"] == "p2"
        assert recorder[1][3] == body
        assert seen_auth == ["Bearer u-mail", "Bearer u-mail"]

    async def test_batch_modify_posts_sparse_body(self, mail, recorder):
        client = mail(lambda r: envelope({}))

        await client.mail.messages.batch_modify(
            "user@example.com",
            message_ids=["m1"],
            add_label_ids=["UNREAD"],
            remove_label_ids=["FLAGGED"],
            add_folder="INBOX",
        )

        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/mail/v1/user_mailboxes/user@example.com/messages/batch_modify")
        assert body == {
            "message_ids": ["m1"],
            "add_label_ids": ["UNREAD"],
            "remove_label_ids": ["FLAGGED"],
            "add_folder": "INBOX",
        }

    async def test_get_by_card_forwards_card_owner_and_user_id_type(self, mail, recorder):
        client = mail(lambda r: envelope({"card_id": "card_1", "message_ids": ["m1"]}))

        result = await client.mail.messages.get_by_card(
            "me", card_id="card_1", owner_id="ou_owner", user_id_type="open_id"
        )

        method, path, params, _ = recorder.last
        assert method == "GET" and path.endswith("/mail/v1/user_mailboxes/me/messages/get_by_card")
        assert params == {"card_id": "card_1", "owner_id": "ou_owner", "user_id_type": "open_id"}
        assert result["message_ids"] == ["m1"]


class TestMailFolders:
    async def test_list_returns_folder_items(self, mail, recorder):
        client = mail(lambda r: envelope({"items": [{"id": "INBOX", "name": "Inbox"}]}))

        result = await client.mail.folders.list("me", folder_type=1)

        method, path, params, _ = recorder.last
        assert method == "GET" and path.endswith("/mail/v1/user_mailboxes/me/folders")
        assert params["folder_type"] == "1"
        assert result[0]["id"] == "INBOX"


class TestMailEvents:
    async def test_subscribe_and_unsubscribe_post_event_type(self, mail, recorder):
        client = mail(lambda r: envelope({}))

        await client.mail.events.subscribe("me")
        await client.mail.events.unsubscribe("me", event_type=1)

        assert recorder[0][0] == "POST"
        assert recorder[0][1].endswith("/mail/v1/user_mailboxes/me/event/subscribe")
        assert recorder[0][3] == {"event_type": 1}
        assert recorder[1][0] == "POST"
        assert recorder[1][1].endswith("/mail/v1/user_mailboxes/me/event/unsubscribe")
        assert recorder[1][3] == {"event_type": 1}
