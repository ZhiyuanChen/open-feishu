"""FeishuClient construction + managed-request behavior at the public seam.

Covers credential resolution and the managed token fetch (``TestManagedToken``),
caller-vs-owned transport lifecycle (``TestLifecycle``), user-scoped views and the
namespace drift guard (``TestAsUser``), token-precedence and non-enveloped responses
(``TestTokenPrecedence``), region storage/resolution (``TestRegion``), and the
``client.cards`` accessor that exposes the card builder and factory helpers
(``TestCardsAccessor``).
"""

import httpx
import pytest

from feishu import FeishuClient
from tests.conftest import envelope


def recording_handler(record):
    """Handler that records every request's url + Authorization header.

    The conftest ``recorder`` captures method/path/body but not auth headers or
    token-fetch requests, so auth-routing tests use this local handler instead.
    """

    def handler(request):
        record.append({"url": str(request.url), "auth": request.headers.get("authorization")})
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "msg": "ok", "tenant_access_token": "t-1", "expire": 7200})
        if "access_token" in request.url.path or request.url.path.endswith("oauth/token"):
            return httpx.Response(200, json={"access_token": "u-1", "expires_in": 7200})
        return httpx.Response(200, json={"code": 0, "msg": "ok", "data": {"ok": True}})

    return handler


@pytest.fixture
async def auth_client():
    """A FeishuClient over a recording transport; yields ``(client, record)``.

    ``record`` is a list of ``{"url", "auth"}`` dicts capturing the Authorization
    header per request, for asserting token routing. Auto-closed after the test.
    """
    record = []
    transport = httpx.AsyncClient(transport=httpx.MockTransport(recording_handler(record)))
    client = FeishuClient("cli_a", "s", transport=transport)
    try:
        yield client, record
    finally:
        await client.aclose()


class TestManagedToken:
    async def test_injects_token(self, client, recorder):
        # A managed request returns data and, transparently, fetched a tenant token first.
        resp = await client.request("GET", "some/v1/thing")
        assert resp["code"] == 0
        method, path, _params, _body = recorder.last
        assert (method, path) == ("GET", "/open-apis/some/v1/thing")

    async def test_from_env(self, monkeypatch):
        # Construct from env only (no positional creds), then prove the creds work by
        # making a request that succeeds — observing behavior, not a private attribute.
        monkeypatch.setenv("FEISHU_APP_ID", "cli_env")
        monkeypatch.setenv("FEISHU_APP_SECRET", "sek")

        seen = {}

        def handler(request):
            if request.url.path.endswith("tenant_access_token/internal"):
                seen["token_body"] = request.read().decode()
                return httpx.Response(200, json={"code": 0, "msg": "ok", "tenant_access_token": "t-1", "expire": 7200})
            return httpx.Response(200, json=envelope({"ok": True}))

        client = FeishuClient(transport=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            resp = await client.request("GET", "some/v1/thing")
            assert resp["data"]["ok"] is True
            assert "cli_env" in seen["token_body"]  # env app_id reached the token fetch
        finally:
            await client.aclose()

    def test_missing_credentials_raises(self, monkeypatch):
        monkeypatch.delenv("FEISHU_APP_ID", raising=False)
        monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
        with pytest.raises(ValueError):
            FeishuClient()


class TestLifecycle:
    async def test_caller_transport_stays_open(self):
        # A caller-supplied httpx client is NOT owned by FeishuClient; exiting the
        # context (or aclose) must leave it open so the caller can reuse it.
        owned = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=envelope({}))))
        async with FeishuClient("cli_a", "s", transport=owned):
            pass
        assert owned.is_closed is False
        await owned.aclose()


class TestAsUser:
    async def test_view_uses_user_token(self, auth_client):
        client, record = auth_client
        await client.im.send("oc_x", {"text": "as the app"})
        await client.as_user("u-bob").im.send("oc_x", {"text": "as the user"})
        sends = [r["auth"] for r in record if "/im/v1/messages" in r["url"]]
        assert sends == ["Bearer t-1", "Bearer u-bob"]  # app->tenant token, view->user token

    async def test_aclose_preserves_transport(self, auth_client):
        client, _record = auth_client
        await client.as_user("u-bob").aclose()  # closing the view must not kill the shared transport
        resp = await client.request("GET", "some/v1/thing")
        assert resp["code"] == 0

    async def test_empty_user_token_raises(self, auth_client):
        client, _record = auth_client
        with pytest.raises(ValueError):
            client.as_user("")

    async def test_rebinds_namespaces(self, auth_client):
        # Drift guard for _NAMESPACE_SLOTS: as_user must reset EVERY lazy namespace cache so each
        # namespace rebinds to the user-scoped view. If a namespace is added to __init__ but omitted
        # from _NAMESPACE_SLOTS, copy.copy would share the base's instance here (bound to the base
        # client -> tenant token), and the identity assertion below catches it.
        client, _record = auth_client
        # fmt: off
        lazy = [
            "approval", "bitable", "board", "calendar", "contact", "docx",
            "drive", "im", "oauth", "sheets", "task", "vc", "wiki",
        ]
        # fmt: on
        warmed = {ns: getattr(client, ns) for ns in lazy}  # warm the base caches
        view = client.as_user("u-x")
        for ns in lazy:
            assert getattr(view, ns) is not warmed[ns], f"{ns} not reset by as_user (missing from _NAMESPACE_SLOTS?)"


class TestTokenPrecedence:
    async def test_none_sends_no_auth(self, auth_client):
        client, record = auth_client
        await client.request("POST", "authen/v2/oauth/token", token_type=None, expect_envelope=False, json={"x": 1})
        assert all("tenant_access_token/internal" not in r["url"] for r in record)  # no tenant fetch
        assert all(r["auth"] is None for r in record)  # no Authorization header

    async def test_non_enveloped_parsed(self, auth_client):
        client, _record = auth_client
        # token_type=None + expect_envelope=False: a bare (non-{code,msg,data}) body is returned as-is.
        resp = await client.request("POST", "authen/v2/oauth/token", token_type=None, expect_envelope=False)
        assert resp["access_token"] == "u-1"

    async def test_user_view_none_stays_anonymous(self, auth_client):
        client, record = auth_client
        # On a user-scoped view, explicit anonymous (token_type=None) must NOT fall back to the user token.
        await client.as_user("u-bob").request(
            "POST", "authen/v2/oauth/token", token_type=None, expect_envelope=False, json={"x": 1}
        )
        assert all(r["auth"] is None for r in record)


class TestRegion:
    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            pytest.param({}, "feishu", id="defaults-to-feishu"),
            pytest.param({"region": "lark"}, "lark", id="explicit-region-stored"),
        ],
    )
    def test_region_resolution(self, kwargs, expected):
        assert FeishuClient("cli_a", "s", **kwargs).region == expected

    def test_custom_region_with_base_url(self):
        # Lazy region: an unknown region + base_url override must construct fine
        # (no eager accounts resolve at construction time).
        client = FeishuClient("cli_a", "s", region="private", base_url="https://feishu.internal.example.com")
        assert client.region == "private"
        assert client.base_url == "https://feishu.internal.example.com"


class TestCardsAccessor:
    async def test_exposes_builder(self, client):
        card = client.cards.Card().header("Hi").markdown("body").to_dict()
        assert card["schema"] == "2.0"
        assert card["header"]["title"]["content"] == "Hi"

    async def test_exposes_factories(self, client):
        assert client.cards.text_card("x")["body"]["elements"][0]["content"] == "x"
