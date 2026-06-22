import pytest

from tests.conftest import envelope, make_client


@pytest.fixture
async def perms(recorder):
    """The drive.permissions namespace wired to `recorder`, echoing data back."""
    client = make_client(recorder=recorder, responder=lambda r: envelope({}))
    try:
        yield client.drive.permissions
    finally:
        await client.aclose()


MEMBER = {"member_type": "openid", "member_id": "ou_1", "perm": "view"}


class TestList:
    async def test_returns_items(self, recorder):
        client = make_client(recorder=recorder, responder=lambda r: envelope({"items": [MEMBER]}))
        resp = await client.drive.permissions.list("doxcabc", doc_type="docx")
        method, path, params, _ = recorder.last
        assert method == "GET" and path.endswith("/drive/v1/permissions/doxcabc/members")
        assert params["type"] == "docx"
        assert resp == [MEMBER]  # not paginated; raw items list
        await client.aclose()

    async def test_returns_empty_when_no_items(self, perms):
        assert await perms.list("doxcabc", doc_type="docx") == []

    async def test_forwards_fields(self, perms, recorder):
        await perms.list("doxcabc", doc_type="docx", fields="*")
        assert recorder.last[2]["fields"] == "*"

    async def test_omits_unset_fields(self, perms, recorder):
        await perms.list("doxcabc", doc_type="docx")
        assert "fields" not in recorder.last[2]


class TestCreate:
    async def test_posts_member(self, recorder):
        client = make_client(recorder=recorder, responder=lambda r: envelope({"member": MEMBER}))
        resp = await client.drive.permissions.create("doxcabc", MEMBER, doc_type="docx")
        method, path, params, body = recorder.last
        assert method == "POST" and path.endswith("/drive/v1/permissions/doxcabc/members")
        assert params["type"] == "docx"
        assert body["member_id"] == "ou_1"
        assert resp["member"]["member_id"] == "ou_1"
        await client.aclose()

    async def test_forwards_need_notification(self, perms, recorder):
        await perms.create("doxcabc", MEMBER, doc_type="docx", need_notification=True)
        assert recorder.last[2]["need_notification"] == "true"

    async def test_omits_unset_need_notification(self, perms, recorder):
        await perms.create("doxcabc", MEMBER, doc_type="docx")
        assert "need_notification" not in recorder.last[2]


class TestDelete:
    async def test_sends_doc_and_member_type(self, perms, recorder):
        resp = await perms.delete("doxcabc", "ou_1", doc_type="docx", member_type="openid")
        method, path, params, _ = recorder.last
        assert method == "DELETE" and path.endswith("/drive/v1/permissions/doxcabc/members/ou_1")
        assert params["type"] == "docx"
        assert params["member_type"] == "openid"
        assert resp == {}

    @pytest.mark.parametrize(
        "member_id, member_type",
        [("ou_bob", "openid"), ("oc_chat", "openchat"), ("od-dept", "opendepartmentid")],
    )
    async def test_infers_member_type_from_prefix(self, perms, recorder, member_id, member_type):
        await perms.delete("doxcabc", member_id, doc_type="docx")
        assert recorder.last[2]["member_type"] == member_type

    async def test_unprefixed_id_requires_member_type(self, perms, recorder):
        with pytest.raises(ValueError):
            await perms.delete("doxcabc", "12345", doc_type="docx")  # userid has no prefix
        assert len(recorder) == 0  # no request issued


class TestPublic:
    async def test_get_public(self, recorder):
        client = make_client(
            recorder=recorder,
            responder=lambda r: envelope({"permission_public": {"external_access": True}}),
        )
        resp = await client.drive.permissions.get_public("doxcabc", doc_type="docx")
        method, path, params, _ = recorder.last
        assert method == "GET" and path.endswith("/drive/v1/permissions/doxcabc/public")
        assert params["type"] == "docx"
        assert resp["permission_public"]["external_access"] is True
        await client.aclose()

    async def test_set_public(self, recorder):
        settings = {"link_share_entity": "tenant_readable"}
        client = make_client(recorder=recorder, responder=lambda r: envelope({"permission_public": settings}))
        resp = await client.drive.permissions.set_public("doxcabc", settings, doc_type="docx")
        method, path, params, body = recorder.last
        assert method == "PATCH" and path.endswith("/drive/v1/permissions/doxcabc/public")
        assert params["type"] == "docx"
        assert body["link_share_entity"] == "tenant_readable"
        assert resp["permission_public"]["link_share_entity"] == "tenant_readable"
        await client.aclose()
