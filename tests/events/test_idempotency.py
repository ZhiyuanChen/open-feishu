import pytest

from feishu.events.idempotency import InMemorySeenStore, SeenStore, claim


class TestInMemorySeenStore:
    async def test_mark_then_seen(self):
        store = InMemorySeenStore()
        assert await store.seen("evt_1") is False
        await store.mark("evt_1")
        assert await store.seen("evt_1") is True
        assert await store.seen("evt_other") is False

    async def test_claim_is_atomic(self):
        # claim() is the atomic check-and-set: first call claims, duplicates return False.
        store = InMemorySeenStore()
        assert await store.claim("evt_1") is True
        assert await store.claim("evt_1") is False
        assert await store.seen("evt_1") is True

    @pytest.mark.parametrize(
        "elapsed, expected",
        [(59.0, True), (61.0, False)],
        ids=["inside-ttl", "past-ttl"],
    )
    async def test_ttl_expiry(self, elapsed, expected):
        clock = {"t": 1000.0}
        store = InMemorySeenStore(ttl=60.0, now=lambda: clock["t"])
        await store.mark("evt_ttl")
        clock["t"] = 1000.0 + elapsed
        assert await store.seen("evt_ttl") is expected

    async def test_implements_protocol(self):
        # runtime_checkable structural check
        assert isinstance(InMemorySeenStore(), SeenStore)


class SeenMarkOnly:
    """A store exposing only seen()/mark() -- no atomic claim()."""

    def __init__(self):
        self._ids: set[str] = set()

    async def seen(self, event_id):
        return event_id in self._ids

    async def mark(self, event_id):
        self._ids.add(event_id)


class TestClaim:
    async def test_seen_mark_only_store_implements_protocol(self):
        assert isinstance(SeenMarkOnly(), SeenStore)

    @pytest.mark.parametrize(
        "store",
        [InMemorySeenStore(), SeenMarkOnly()],
        ids=["atomic-claim", "seen-mark-fallback"],
    )
    async def test_first_seen_then_duplicate(self, store):
        assert await claim(store, "evt_1") is True  # first-seen -> process
        assert await claim(store, "evt_1") is False  # duplicate -> skip
