"""InternalCredential token fetching and cache-key non-collision.

The cache key string is an internal artifact, so instead of asserting its shape
these tests assert the observable consequence: tokens for different apps,
token-types, or hosts do not collide -- each dimension gets its own cached entry
and its own fetch. The ``fetch`` contract (returns ``(token, expire)``) is
asserted directly since callers/the TokenManager depend on that tuple.
"""

import json

import httpx
import pytest

from feishu._transport import RetryPolicy, Transport
from feishu.auth.credentials import (
    InMemoryAppTicketStore,
    InternalCredential,
    StoreCredential,
)
from feishu.auth.tokens import InMemoryTokenCache, TokenManager
from feishu.errors import FeishuError


def counting_token_transport(counter, base_url="https://open.feishu.cn"):
    """A ``Transport`` whose token endpoint returns a fresh ``t-N`` per fetch and counts fetches."""

    def handler(request):
        counter["n"] += 1
        # The credential fetches /auth/v3/{tenant|app}_access_token/internal; echo the matching key.
        token_type = "app" if "app_access_token" in request.url.path else "tenant"
        return httpx.Response(
            200, json={"code": 0, "msg": "ok", f"{token_type}_access_token": f"t-{counter['n']}", "expire": 7200}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Transport(base_url, timeout=5.0, retry=RetryPolicy.default(), client=client)


def expire_token_transport(expire):
    """A ``Transport`` whose token endpoint echoes a caller-chosen (possibly invalid) ``expire``."""

    def handler(request):
        return httpx.Response(200, json={"code": 0, "msg": "ok", "tenant_access_token": "t-1", "expire": expire})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Transport("https://open.feishu.cn", timeout=5.0, retry=RetryPolicy.default(), client=client)


class TestInternalCredentialValidation:
    @pytest.mark.parametrize("app_id, app_secret", [("", "secret"), ("cli_x", "")])
    def test_empty_field_raises(self, app_id, app_secret):
        # Contract: any empty field must raise immediately (no partial construction).
        with pytest.raises(ValueError):
            InternalCredential(app_id, app_secret)

    async def test_bogus_token_type_via_request(self):
        # Contract: requesting an unsupported token_type surfaces a ValueError
        # through the client.request public path (token_type flows to credential.fetch).
        from tests.conftest import make_client

        client = make_client()
        with pytest.raises(ValueError, match="unsupported token_type"):
            await client.request("GET", "im/v1/messages", token_type="bogus")
        await client.aclose()


class TestFetch:
    async def test_returns_token_and_expiry(self):
        counter = {"n": 0}
        cred = InternalCredential("cli_a", "secret")
        token, expire = await cred.fetch(counting_token_transport(counter), "tenant")
        # The fetch contract is the (token, expiry-seconds) tuple the cache stores.
        assert token == "t-1"
        assert expire == 7200

    async def test_accepts_tiny_positive_expire(self):
        # A short-lived token is still a valid token: a positive expire must pass through
        # untouched (the manager, not the credential, is responsible for caching it briefly).
        cred = InternalCredential("cli_a", "secret")
        token, expire = await cred.fetch(expire_token_transport(1), "tenant")
        assert token == "t-1"
        assert expire == 1

    @pytest.mark.parametrize("garbage", [0, -100, "oops", None, True, 1.5])
    async def test_rejects_garbage_expire(self, garbage):
        # A non-positive / non-int expire would compute a past or nonsensical expire_at and
        # be cached as already-expired; reject it loudly instead of trusting int(envelope[...]).
        cred = InternalCredential("cli_a", "secret")
        with pytest.raises(FeishuError, match="invalid token expire"):
            await cred.fetch(expire_token_transport(garbage), "tenant")


class TestCacheNonCollision:
    """Distinct app / token-type / host dimensions get independent cached tokens."""

    async def test_different_hosts(self):
        counter = {"n": 0}
        cred = InternalCredential("cli_a", "secret")
        # A shared cache: if host did not distinguish keys, the second manager
        # would reuse the first's token and only one fetch would occur.
        shared = InMemoryTokenCache()
        cn = TokenManager(cred, counting_token_transport(counter, "https://open.feishu.cn"), cache=shared)
        intl = TokenManager(cred, counting_token_transport(counter, "https://open.larksuite.com"), cache=shared)
        cn_token = await cn.tenant_access_token()
        intl_token = await intl.tenant_access_token()
        # Each host is a distinct cache entry -> two separate fetches, no cross-host reuse.
        assert cn_token != intl_token
        assert counter["n"] == 2

    async def test_different_token_types(self):
        counter = {"n": 0}
        cred = InternalCredential("cli_a", "secret")
        mgr = TokenManager(cred, counting_token_transport(counter))
        tenant = await mgr.token("tenant")
        app = await mgr.token("app")
        # tenant and app tokens are cached separately -> two fetches.
        assert tenant != app
        assert counter["n"] == 2

    async def test_same_dimensions_reuse(self):
        counter = {"n": 0}
        cred = InternalCredential("cli_a", "secret")
        mgr = TokenManager(cred, counting_token_transport(counter))
        first = await mgr.tenant_access_token()
        second = await mgr.tenant_access_token()
        # Identical dimensions collapse to one cached entry.
        assert first == second
        assert counter["n"] == 1


def recording_store_transport(recorder, base_url="https://open.feishu.cn"):
    """A ``Transport`` for the ISV endpoints: records each ``(path, body)`` and returns canned tokens.

    The handler dispatches on path: app_access_token -> ``a-1``, tenant_access_token -> ``t-1``,
    app_ticket/resend -> bare ``{code: 0}``. Recording lets tests assert which endpoints were hit,
    in what order, and that secrets/tickets travel in the body (never as an auth header).
    """

    def handler(request):
        path = request.url.path
        recorder.append((path, json.loads(request.content or b"{}"), request.headers.get("authorization")))
        if path.endswith("auth/v3/app_access_token"):
            return httpx.Response(200, json={"code": 0, "msg": "ok", "app_access_token": "a-1", "expire": 7200})
        if path.endswith("auth/v3/tenant_access_token"):
            return httpx.Response(200, json={"code": 0, "msg": "ok", "tenant_access_token": "t-1", "expire": 7200})
        if path.endswith("auth/v3/app_ticket/resend"):
            return httpx.Response(200, json={"code": 0, "msg": "ok"})
        return httpx.Response(404, json={"code": -1, "msg": "unexpected path"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Transport(base_url, timeout=5.0, retry=RetryPolicy.default(), client=client)


class TestStoreCredentialValidation:
    @pytest.mark.parametrize(
        "app_id, app_secret, tenant_key",
        [("", "secret", "tk"), ("cli_x", "", "tk"), ("cli_x", "secret", "")],
    )
    def test_empty_field_raises(self, app_id, app_secret, tenant_key):
        # Contract: any empty field must raise immediately (no partial construction).
        with pytest.raises(ValueError):
            StoreCredential(app_id, app_secret, tenant_key)

    async def test_bogus_token_type_raises(self):
        # Contract: an unsupported token_type surfaces a ValueError from fetch.
        recorder = []
        cred = StoreCredential("cli_x", "secret", "tk")
        with pytest.raises(ValueError, match="unsupported token_type"):
            await cred.fetch(recording_store_transport(recorder), "bogus")


class TestStoreCredentialCacheKey:
    def test_includes_tenant_key(self):
        # Tenant isolation: two tenants of the same app must get distinct cache keys, so one
        # tenant's tenant_access_token can never overwrite another's.
        cred_a = StoreCredential("cli_x", "secret", "tenant-a")
        cred_b = StoreCredential("cli_x", "secret", "tenant-b")
        key_a = cred_a.cache_key("tenant", "https://open.feishu.cn")
        key_b = cred_b.cache_key("tenant", "https://open.feishu.cn")
        assert key_a != key_b
        assert "tenant-a" in key_a
        assert "tenant-b" in key_b


class TestStoreCredentialFetch:
    async def test_app_posts_directly(self):
        store = InMemoryAppTicketStore()
        await store.set("cli_x", "ticket-1")
        cred = StoreCredential("cli_x", "secret", "tk", app_ticket_store=store)
        recorder = []
        token, expire = await cred.fetch(recording_store_transport(recorder), "app")
        assert (token, expire) == ("a-1", 7200)
        # Exactly the app_access_token endpoint, with app_id/app_secret/app_ticket in the body
        # and no auth header (body-authenticated).
        assert len(recorder) == 1
        path, body, authorization = recorder[0]
        assert path.endswith("auth/v3/app_access_token")
        assert (body["app_id"], body["app_secret"], body["app_ticket"]) == ("cli_x", "secret", "ticket-1")
        assert authorization is None

    async def test_tenant_chains_app_then_tenant(self):
        store = InMemoryAppTicketStore()
        await store.set("cli_x", "ticket-1")
        cred = StoreCredential("cli_x", "secret", "tk", app_ticket_store=store)
        recorder = []
        token, expire = await cred.fetch(recording_store_transport(recorder), "tenant")
        assert (token, expire) == ("t-1", 7200)
        # First app_access_token, then tenant_access_token carrying that app_access_token + tenant_key.
        assert [path for path, _, _ in recorder] == [
            "/open-apis/auth/v3/app_access_token",
            "/open-apis/auth/v3/tenant_access_token",
        ]
        tenant_body = recorder[1][1]
        assert (tenant_body["app_access_token"], tenant_body["tenant_key"]) == ("a-1", "tk")
        # Credentials are body-authenticated: neither request may carry an auth header.
        assert all(authorization is None for _, _, authorization in recorder)

    async def test_missing_ticket_resends(self):
        # Empty store: a resend must be requested AND a FeishuError raised so the caller retries
        # once the app_ticket event lands.
        cred = StoreCredential("cli_x", "secret", "tk")  # default empty in-memory store
        recorder = []
        with pytest.raises(FeishuError, match="app_ticket unavailable"):
            await cred.fetch(recording_store_transport(recorder), "app")
        assert len(recorder) == 1
        path, body, authorization = recorder[0]
        assert path.endswith("auth/v3/app_ticket/resend")
        assert (body["app_id"], body["app_secret"]) == ("cli_x", "secret")
        assert authorization is None  # ticket/secret travel in the body, never as an auth header


class TestInMemoryAppTicketStore:
    async def test_get_set_round_trip(self):
        store = InMemoryAppTicketStore()
        assert await store.get("cli_x") is None
        await store.set("cli_x", "ticket-1")
        assert await store.get("cli_x") == "ticket-1"
