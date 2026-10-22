"""OAuth continuations settle durable jobs without losing uncertain executions."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from feishu.agent import oauth
from feishu.agent.loop import AgentEngine
from feishu.agent.persistence import SqlitePendingAuthorizationStore, SqliteSessionStore
from feishu.agent.tools import ToolRegistry
from feishu.events.job_runner import EventJobRunner
from feishu.events.jobs import EventJobStatus, SqliteEventJobStore
from tests._fakes import FakeLlmBackend, text_turn, tool_turn
from tests.agent.test_loop import _ApprovalRecordingClient, _text_event

_AUTHENTICATED_USER = object()


@pytest.fixture
def durable_stores(tmp_path):
    path = tmp_path / "oauth.db"
    stores = SimpleNamespace(
        authorizations=SqlitePendingAuthorizationStore(path),
        sessions=SqliteSessionStore(path),
        jobs=SqliteEventJobStore(path),
    )
    yield stores
    for store in vars(stores).values():
        store._db.close()


async def _pending(stores, handler):
    proposals = []
    registry = ToolRegistry()
    registry.register(
        "write",
        handler,
        description="test operation",
        input_schema={"type": "object", "properties": {}},
        auth_scopes=("scope",),
    )
    client = _ApprovalRecordingClient()
    agent = AgentEngine(
        backend=FakeLlmBackend([tool_turn(id="call", name="write", arguments_json="{}"), text_turn("done")]),
        registry=registry,
        store=stores.sessions,
        authorizations=stores.authorizations,
        client=client,
        auth_card_builder=lambda url: {"url": url},
        authorize_url_builder=lambda user, scopes, pending: proposals.append(pending) or "https://example.invalid/auth",
    )
    await agent.run(_text_event("write"))
    return agent, proposals[0], client


async def _runner(stores, agent, pending, *, user=_AUTHENTICATED_USER):
    await stores.jobs.enqueue(
        "resume",
        {"header": {"event_type": "test", "event_id": "resume"}, "event": {}},
        partition_key=pending.session_id,
    )
    results = []

    async def consume(_event):
        result = await oauth.resume_authorization(
            agent,
            pending.authorization_id,
            user={"open_id": "ou_tester"} if user is _AUTHENTICATED_USER else user,
            propagate_errors=True,
        )
        results.append(result)

    runner = EventJobRunner(stores.jobs, consume, worker_id="worker", backoff_base_seconds=0)
    return runner, results


async def test_resumed_tool_exception_freezes_job_and_pending_without_progress_ui(durable_stores):
    writes = []

    async def write():
        writes.append("submitted")
        raise RuntimeError("response lost")

    agent, pending, _client = await _pending(durable_stores, write)
    runner, results = await _runner(durable_stores, agent, pending)
    job = await runner.run_once()

    assert job.status is EventJobStatus.UNKNOWN
    assert job.side_effect_started
    assert job.error_type == "RuntimeError"
    assert (await durable_stores.authorizations.get(pending.authorization_id)).state == "execution_unknown"
    assert results == []
    assert await runner.run_once() is None
    assert writes == ["submitted"]


@pytest.mark.parametrize("phase", ["history", "card", "tool", "completion"])
async def test_post_claim_failure_propagates_and_freezes_pending(durable_stores, monkeypatch, phase):
    writes = []

    async def write():
        writes.append("submitted")
        if phase == "tool":
            raise RuntimeError("tool unavailable")
        return "written"

    agent, pending, _client = await _pending(durable_stores, write)
    if phase == "history":

        async def fail_history(_session_id):
            raise RuntimeError("history unavailable")

        monkeypatch.setattr(agent.store, "get", fail_history)
    elif phase == "card":

        async def fail_card(*_args, **_kwargs):
            raise RuntimeError("card cleanup unavailable")

        monkeypatch.setattr(oauth, "remove_authorization_card", fail_card)
    elif phase == "completion":
        original_complete = agent.authorizations.complete

        async def fail_complete(authorization_id, *, outcome):
            if outcome == "executed":
                raise RuntimeError("completion unavailable")
            await original_complete(authorization_id, outcome=outcome)

        monkeypatch.setattr(agent.authorizations, "complete", fail_complete)
    runner, results = await _runner(durable_stores, agent, pending)
    job = await runner.run_once()

    assert job.status is EventJobStatus.UNKNOWN
    assert job.error_type == "RuntimeError"
    assert (await durable_stores.authorizations.get(pending.authorization_id)).state == "execution_unknown"
    assert results == []
    assert writes == (["submitted"] if phase in ("tool", "completion") else [])


@pytest.mark.parametrize("phase", ["claim", "history", "tool"])
async def test_cancellation_after_fence_never_becomes_completed_on_restart(durable_stores, monkeypatch, phase):
    blocked = asyncio.Event()
    writes = []

    async def write():
        writes.append("submitted")
        if phase == "tool":
            blocked.set()
            await asyncio.Event().wait()
        return "written"

    agent, pending, _client = await _pending(durable_stores, write)
    if phase == "claim":

        async def blocked_claim(_authorization_id):
            blocked.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(agent.authorizations, "claim", blocked_claim)
    elif phase == "history":

        async def blocked_history(_session_id):
            blocked.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(agent.store, "get", blocked_history)
    runner, _results = await _runner(durable_stores, agent, pending)
    task = asyncio.create_task(runner.run_once())
    try:
        await asyncio.wait_for(blocked.wait(), timeout=1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    job = await durable_stores.jobs.get("resume")
    assert job.status is EventJobStatus.UNKNOWN
    assert job.side_effect_started
    assert (await durable_stores.authorizations.get(pending.authorization_id)).state == "execution_unknown"
    reopened = SqliteEventJobStore(durable_stores.jobs._db.execute("PRAGMA database_list").fetchone()[2])
    try:
        restarted = EventJobRunner(reopened, lambda _event: asyncio.sleep(0), worker_id="restart")
        assert await restarted.run_once() is None
    finally:
        reopened._db.close()
    assert writes == (["submitted"] if phase == "tool" else [])


async def test_fence_failure_does_not_claim_pending_or_dispatch_tool(durable_stores, monkeypatch):
    writes = []

    async def write():
        writes.append("submitted")
        return "written"

    agent, pending, _client = await _pending(durable_stores, write)

    async def fail_fence(_job_id, _worker_id):
        raise RuntimeError("lease fence unavailable")

    monkeypatch.setattr(durable_stores.jobs, "mark_side_effect_started", fail_fence)
    runner, results = await _runner(durable_stores, agent, pending)
    job = await runner.run_once()

    assert job.status is EventJobStatus.RETRY_WAIT
    assert not job.side_effect_started
    assert job.error_type == "RuntimeError"
    assert (await durable_stores.authorizations.get(pending.authorization_id)).state == "awaiting_authorization"
    assert writes == []
    assert results == []


@pytest.mark.parametrize("user", [None, {}, {"open_id": "ou_other"}])
async def test_invalid_resume_owner_leaves_pending_unclaimed(durable_stores, user):
    writes = []

    async def write():
        writes.append("submitted")
        return "written"

    agent, pending, client = await _pending(durable_stores, write)
    runner, results = await _runner(durable_stores, agent, pending, user=user)
    job = await runner.run_once()

    assert job.status is EventJobStatus.COMPLETED
    assert not job.side_effect_started
    assert (await durable_stores.authorizations.get(pending.authorization_id)).state == "awaiting_authorization"
    assert results == ["forbidden"]
    assert writes == []
    assert client.replies == []


async def test_missing_authorization_does_not_mark_side_effect(durable_stores):
    async def write():
        raise AssertionError("missing authorization must not dispatch")

    agent, pending, _client = await _pending(durable_stores, write)
    await durable_stores.authorizations.complete(pending.authorization_id, outcome="cancelled")
    runner, results = await _runner(durable_stores, agent, pending)
    job = await runner.run_once()

    assert job.status is EventJobStatus.COMPLETED
    assert not job.side_effect_started
    assert results == ["missing"]


async def test_success_fences_claim_and_records_completed_continuation(durable_stores, monkeypatch):
    writes = []

    async def write():
        writes.append("submitted")
        return "written"

    agent, pending, client = await _pending(durable_stores, write)
    original_claim = agent.authorizations.claim

    async def claim_after_fence(authorization_id):
        job = await durable_stores.jobs.get("resume")
        assert job.side_effect_started
        return await original_claim(authorization_id)

    monkeypatch.setattr(agent.authorizations, "claim", claim_after_fence)
    runner, results = await _runner(durable_stores, agent, pending)
    job = await runner.run_once()

    assert job.status is EventJobStatus.COMPLETED
    assert job.side_effect_started
    assert await durable_stores.authorizations.get(pending.authorization_id) is None
    assert results == ["resumed"]
    assert writes == ["submitted"]
    assert client.replies[-1][1] == "done"


async def test_default_local_resume_still_reports_tool_failure_to_model(durable_stores):
    async def write():
        raise RuntimeError("synthetic tool failure")

    agent, pending, client = await _pending(durable_stores, write)
    result = await oauth.resume_authorization(agent, pending.authorization_id, user={"open_id": "ou_tester"})

    assert result == "resumed"
    assert await durable_stores.authorizations.get(pending.authorization_id) is None
    assert client.replies[-1][1] == "done"
