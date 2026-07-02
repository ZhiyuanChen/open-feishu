import asyncio

import pytest

from feishu.agent.llm import Message, TextPart
from feishu.agent.session import (
    InMemoryPendingApprovalStore,
    InMemoryPendingAuthorizationStore,
    InMemorySessionStore,
    PendingApproval,
    PendingApprovalStore,
    PendingAuthorization,
    PendingAuthorizationStore,
    SessionStore,
)


def msg(text, role="user"):
    return Message(role=role, content=[TextPart(text=text)])


@pytest.mark.parametrize(
    "impl, proto",
    [
        (InMemorySessionStore, SessionStore),
        (InMemoryPendingApprovalStore, PendingApprovalStore),
        (InMemoryPendingAuthorizationStore, PendingAuthorizationStore),
    ],
)
def test_satisfies_protocol(impl, proto):
    assert isinstance(impl(), proto)


class TestSessionStore:
    @pytest.fixture
    def store(self):
        return InMemorySessionStore()

    async def test_get_unknown_is_empty(self, store):
        assert await store.get("chat_x") == []

    async def test_append_preserves_order(self, store):
        m1, m2 = msg("hi"), msg("hello", role="assistant")
        await store.append("s", m1)
        await store.append("s", m2)
        assert await store.get("s") == [m1, m2]

    async def test_append_variadic(self, store):
        a, b = msg("a"), msg("b", role="tool")
        await store.append("s", a, b)
        assert await store.get("s") == [a, b]

    async def test_set_replaces_history(self, store):
        await store.append("s", msg("old"))
        replacement = [msg("new", role="assistant")]
        await store.set("s", replacement)
        assert await store.get("s") == replacement

    async def test_get_returns_copy(self, store):
        await store.append("s", msg("hi"))
        got = await store.get("s")
        got.append(msg("mutation"))
        assert len(await store.get("s")) == 1

    async def test_concurrent_appends_keep_all(self, store):
        await asyncio.gather(*[store.append("s", msg(str(i))) for i in range(50)])
        assert len(await store.get("s")) == 50


class TestPendingApprovalStore:
    @pytest.fixture
    def store(self):
        return InMemoryPendingApprovalStore()

    async def test_put_pop_consumes_once(self, store):
        pa = PendingApproval(
            approval_id="ap_1",
            session_id="s",
            tool_call_id="c1",
            tool_name="deploy",
            arguments={"env": "prod"},
        )
        await store.put(pa)
        assert await store.pop("ap_1") == pa
        assert await store.pop("ap_1") is None

    async def test_pop_unknown_is_none(self, store):
        assert await store.pop("nope") is None


class TestPendingAuthorizationStore:
    @pytest.fixture
    def store(self):
        return InMemoryPendingAuthorizationStore()

    async def test_put_pop_consumes_once(self, store):
        pa = PendingAuthorization(
            authorization_id="az_1",
            session_id="s",
            tool_call_id="c1",
            tool_name="list_calendar_events",
            arguments={},
            scopes=("calendar:calendar",),
        )
        await store.put(pa)
        assert await store.pop("az_1") == pa
        assert await store.pop("az_1") is None

    async def test_claim_consumes_once(self, store):
        pa = PendingAuthorization(
            authorization_id="az_1",
            session_id="s",
            tool_call_id="c1",
            tool_name="list_calendar_events",
            arguments={},
        )
        await store.put(pa)
        assert (await store.claim("az_1")).value == "claimed"
        assert (await store.claim("az_1")).value == "already_claimed"
