"""Token caching, refresh, and concurrent de-duplication.

The contract a caller relies on: a managed token is fetched once and reused, it
is refetched when it nears expiry, and a burst of concurrent callers triggers a
single fetch (no thundering herd). The headline assertion is observable -- the
number of times the token endpoint is hit -- exercised both at the
``FeishuClient.request`` boundary and on ``TokenManager`` as a thin companion.
"""

import asyncio

import httpx
import pytest

from feishu import FeishuClient
from feishu._transport import RetryPolicy, Transport
from feishu.auth.credentials import InternalCredential
from feishu.auth.tokens import TokenManager


def counting_token_transport(counter, expire=7200):
    """A ``Transport`` whose token endpoint returns a fresh ``t-N`` per fetch and counts fetches."""

    def handler(request):
        counter["n"] += 1
        # The credential fetches /auth/v3/{tenant|app}_access_token/internal; echo the matching key.
        token_type = "app" if "app_access_token" in request.url.path else "tenant"
        return httpx.Response(
            200, json={"code": 0, "msg": "ok", f"{token_type}_access_token": f"t-{counter['n']}", "expire": expire}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Transport("https://open.feishu.cn", timeout=5.0, retry=RetryPolicy.default(), client=client)


class TestTokenManager:
    def make_manager(self, counter, clock=None, **kwargs):
        if clock is not None:
            kwargs["now"] = lambda: clock["t"]
        expire = kwargs.pop("expire", 7200)
        return TokenManager(InternalCredential("cli_a", "s"), counting_token_transport(counter, expire), **kwargs)

    async def test_fetched_once_and_reused(self):
        counter = {"n": 0}
        mgr = self.make_manager(counter)
        first = await mgr.tenant_access_token()
        second = await mgr.tenant_access_token()
        assert first == second
        assert counter["n"] == 1

    async def test_refetched_near_expiry(self):
        counter, clock = {"n": 0}, {"t": 0.0}
        mgr = self.make_manager(counter, clock, refresh_offset=1800)
        first = await mgr.tenant_access_token()  # valid until 0 + 7200 - 1800 = 5400
        clock["t"] = 5401.0  # advance past the refresh point
        second = await mgr.tenant_access_token()
        assert first != second
        assert counter["n"] == 2

    async def test_concurrent_callers_collapse_to_one_fetch(self):
        counter = {"n": 0}
        mgr = self.make_manager(counter)
        results = await asyncio.gather(*[mgr.tenant_access_token() for _ in range(20)])
        # All concurrent callers receive the same token from one fetch.
        assert set(results) == {"t-1"}
        assert counter["n"] == 1

    async def test_distinct_types_resolve_without_deadlock(self):
        counter = {"n": 0}
        mgr = self.make_manager(counter)
        # Two unrelated token types requested concurrently both resolve (no serialization deadlock).
        tenant, app = await asyncio.gather(mgr.token("tenant"), mgr.token("app"))
        assert tenant and app

    @pytest.mark.parametrize(
        "at, reused",
        [
            (5.0, True),  # within the clamped 10s window -> still valid
            (11.0, False),  # past the clamp window -> treated as expired
        ],
    )
    async def test_short_token_clamped_to_min_ttl(self, at, reused):
        # expire (60) <= refresh_offset (1800): without the clamp, expires_at = now + 60 - 1800
        # lands in the past, so the next read sees it expired and re-fetches (stampede). The
        # min_ttl clamp keeps it cached for a small positive window instead.
        counter, clock = {"n": 0}, {"t": 0.0}
        mgr = self.make_manager(counter, clock, expire=60, refresh_offset=1800, min_ttl=10)
        first = await mgr.tenant_access_token()
        clock["t"] = at
        second = await mgr.tenant_access_token()
        assert (first == second) is reused
        assert counter["n"] == (1 if reused else 2)


class TestManagedTokenThroughClient:
    async def test_token_fetched_once_across_requests(self):
        counter = {"n": 0}

        def handler(request):
            if request.url.path.endswith("tenant_access_token/internal"):
                counter["n"] += 1
                return httpx.Response(200, json={"code": 0, "msg": "ok", "tenant_access_token": "t-1", "expire": 7200})
            return httpx.Response(200, json={"code": 0, "msg": "ok", "data": {}})

        client = FeishuClient("cli_test", "secret", transport=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        await client.request("GET", "im/v1/messages/a")
        await client.request("GET", "im/v1/messages/b")
        # The managed tenant token is shared across calls: one fetch, two requests.
        assert counter["n"] == 1
        await client.aclose()
