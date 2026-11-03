from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from feishu.auth.user_tokens import InMemoryOAuthTokenStore, SqliteOAuthTokenStore, UserTokenProvider


@pytest.fixture(params=("memory", "sqlite"))
def store(request, tmp_path):
    return InMemoryOAuthTokenStore() if request.param == "memory" else SqliteOAuthTokenStore(tmp_path / "tokens.db")


async def test_tenant_isolation(store):
    user = {"user_id": "alice"}
    await store.save({"access_token": "legacy"}, user_info=user)
    assert await store.get_for_tenant("a", user) is None
    await store.save_for_tenant("a", {"access_token": "a"}, user_keys=("user_id:alice",))
    await store.save_for_tenant("b", {"access_token": "b"}, user_keys=("user_id:alice",))
    assert (await store.get_for_tenant("a", user)).user_access_token == "a"
    assert (await store.get_for_tenant("b", user)).user_access_token == "b"
    assert (await store.get(user)).user_access_token == "legacy"
    with pytest.raises(ValueError):
        await store.get_for_tenant("a", {**user, "tenant_key": "b"})
    with pytest.raises(ValueError):
        await store.save_for_tenant("a", {"access_token": "bad"}, user_info={**user, "tenant_key": "b"})


async def test_concurrent_refresh(store, tmp_path):
    user = {"user_id": "alice"}
    await store.save_for_tenant(
        "a", {"access_token": "old", "refresh_token": "r", "expires_in": 0}, user_keys=("user_id:alice",)
    )
    calls = []

    async def refresh(token):
        calls.append(token)
        await asyncio.sleep(0.05)
        return {"access_token": "new", "refresh_token": "new-r", "expires_in": 3600}

    client = SimpleNamespace(oauth=SimpleNamespace(refresh=refresh), as_user=lambda token: token)
    other = SqliteOAuthTokenStore(tmp_path / "tokens.db") if isinstance(store, SqliteOAuthTokenStore) else store
    first, second = UserTokenProvider(client, store), UserTokenProvider(client, other)
    result = await asyncio.gather(first.user_token_for_tenant("a", user), second.user_token_for_tenant("a", user))
    assert result == ["new", "new"]
    assert calls == ["r"]


async def test_refresh_failure_freezes(store, tmp_path):
    user = {"user_id": "alice"}
    await store.save_for_tenant(
        "a", {"access_token": "old", "refresh_token": "r", "expires_in": 0}, user_keys=("user_id:alice",)
    )
    calls = []

    async def refresh(token):
        calls.append(token)
        raise ConnectionError("outcome unknown")

    provider = UserTokenProvider(SimpleNamespace(oauth=SimpleNamespace(refresh=refresh)), store)
    assert await provider.user_token_for_tenant("a", user) is None
    other = SqliteOAuthTokenStore(tmp_path / "tokens.db") if isinstance(store, SqliteOAuthTokenStore) else store
    reopened = UserTokenProvider(SimpleNamespace(oauth=SimpleNamespace(refresh=refresh)), other)
    assert await reopened.user_token_for_tenant("a", user) is None
    assert calls == ["r"]


async def test_reauth_during_refresh(store):
    user = {"user_id": "alice"}
    await store.save_for_tenant(
        "a", {"access_token": "old", "refresh_token": "r", "expires_in": 0}, user_keys=("user_id:alice",)
    )
    started, release = asyncio.Event(), asyncio.Event()

    async def refresh(_token):
        started.set()
        await release.wait()
        return {"access_token": "stale", "refresh_token": "stale-r", "expires_in": 3600}

    provider = UserTokenProvider(SimpleNamespace(oauth=SimpleNamespace(refresh=refresh)), store)
    refreshing = asyncio.create_task(provider.user_token_for_tenant("a", user))
    await started.wait()
    await store.save_for_tenant("a", {"access_token": "reauthorized", "expires_in": 3600}, user_keys=("user_id:alice",))
    release.set()
    assert await refreshing == "reauthorized"
    assert (await store.get_for_tenant("a", user)).user_access_token == "reauthorized"


async def test_legacy_store_fails_closed():
    class LegacyStore:
        async def get(self, user):
            raise AssertionError("unscoped store must not be read")

    provider = UserTokenProvider(SimpleNamespace(), LegacyStore())
    assert await provider.user_token_for_tenant("a", {"user_id": "alice"}) is None
