import time

from feishu.agent.persistence import SqlitePendingApprovalStore, SqlitePendingAuthorizationStore
from feishu.agent.session import ClaimResult, PendingApproval, PendingAuthorization


async def test_scheduled_expiry(tmp_path, monkeypatch):
    created = int(time.time())
    store = SqlitePendingApprovalStore(tmp_path / "approval.db", ttl_seconds=1)
    pending = PendingApproval("ap", "scheduled:task:run", "call", "write", {}, created_at=created)
    await store.put(pending)
    await store.put(PendingApproval("ordinary", "chat", "call", "write", {}, created_at=created))
    monkeypatch.setattr("feishu.agent.persistence._now", lambda: created + 2)
    assert await store.get("ordinary") is None
    assert await store.get("ap") is not None
    assert await store.expire_if_pending("ap", now=created + 1799) == "not_due"
    assert await store.expire_if_pending("ap", now=created + 1800) == "expired"
    assert await store.expire_if_pending("ap", now=created + 1801) == "expired"
    assert await store.claim("ap") == ClaimResult.EXPIRED
    await store.put(PendingApproval("active", "scheduled:task:run", "call", "write", {}, created_at=created))
    assert await store.claim("active") == ClaimResult.CLAIMED
    assert await store.expire_if_pending("active", now=created + 2000) == "executing"
    await store.complete("active", outcome="unknown")
    assert await store.expire_if_pending("active", now=created + 2000) == "unknown"
    store._db.close()


async def test_scheduled_auth_expiry(tmp_path, monkeypatch):
    created = int(time.time())
    store = SqlitePendingAuthorizationStore(tmp_path / "auth.db", ttl_seconds=1)
    pending = PendingAuthorization("az", "scheduled:task:run", "call", "write", {}, created_at=created)
    await store.put(pending)
    await store.put(PendingAuthorization("ordinary", "chat", "call", "write", {}, created_at=created))
    monkeypatch.setattr("feishu.agent.persistence._now", lambda: created + 2)
    assert await store.claim("ordinary") == ClaimResult.EXPIRED
    assert await store.get("az") is not None
    assert await store.expire_if_pending("az", now=created + 3599) == "not_due"
    assert await store.expire_if_pending("az", now=created + 3600) == "expired"
    assert await store.expire_if_pending("az", now=created + 3601) == "expired"
    assert await store.claim("az") == ClaimResult.EXPIRED
    await store.put(PendingAuthorization("active", "scheduled:task:run", "call", "write", {}, created_at=created))
    assert await store.claim("active") == ClaimResult.CLAIMED
    assert await store.expire_if_pending("active", now=created + 4000) == "executing"
    await store.complete("active", outcome="unknown")
    assert await store.expire_if_pending("active", now=created + 4000) == "unknown"
    store._db.close()
