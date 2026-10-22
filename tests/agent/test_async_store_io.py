"""Async managed stores retain approval replay, audit, and notification semantics."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from feishu.agent.approval import ApprovalStatus, DefaultApprovalEngine
from feishu.agent.integrity import payload_sha256
from feishu.agent.persistence import JsonlAuditLog, SqliteExecutionResultStore
from feishu.agent.result import ToolOutcome, ToolResult
from feishu.agent.session import InMemoryPendingApprovalStore, PendingApproval
from feishu.gateway.notifications import upsert_interactive_card


class AsyncExecutions:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    async def get(self, lookup_key: str) -> dict[str, Any] | None:
        await asyncio.sleep(0)
        return self.records.get(lookup_key)

    async def put(
        self,
        idempotency_key: str,
        *,
        execution_status: str,
        result: Any,
        alias_lookup_keys: tuple[str, ...] = (),
        payload_sha256: str | None = None,
    ) -> None:
        await asyncio.sleep(0)
        record = {"execution_status": execution_status, "result": result, "payload_sha256": payload_sha256}
        for key in (idempotency_key, *alias_lookup_keys):
            self.records[key] = record


class AsyncAudit:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def append(
        self,
        event_type: str,
        *,
        key: str,
        approval: PendingApproval | None = None,
        event_id: str | None = None,
        message_id: str | None = None,
        outcome: str = "ok",
        error: str | None = None,
    ) -> None:
        await asyncio.sleep(0)
        self.events.append(event_type)


def _pending(approval_id: str) -> PendingApproval:
    return PendingApproval(
        approval_id=approval_id,
        session_id="chat",
        tool_call_id="call",
        tool_name="deploy",
        arguments={"env": "prod"},
        payload_sha256=payload_sha256({"env": "prod"}),
        idempotency_key="stable-deployment",
    )


def _no_threads(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("an async store must run on the event loop")


async def test_async_approval_execution_replay_and_audit_do_not_use_threads(monkeypatch):
    monkeypatch.setattr(asyncio, "to_thread", _no_threads)
    executions, audit = AsyncExecutions(), AsyncAudit()
    engine = DefaultApprovalEngine(approvals=InMemoryPendingApprovalStore(), executions=executions, audit=audit)
    calls = []

    async def dispatch(name, arguments):
        calls.append((name, arguments))
        return ToolResult(outcome=ToolOutcome.COMPLETED, content={"deployment": "done"})

    first = _pending("first")
    await engine.on_request(first)
    executed = await engine.on_decision(
        first.approval_id, "approve", expected_payload_sha256=first.payload_sha256, dispatch=dispatch
    )
    assert executed.status is ApprovalStatus.EXECUTED
    assert list(executions.records.values())[0]["result"] == {"deployment": "done"}
    repeated = _pending("repeated")
    await engine.on_request(repeated)
    replayed = await engine.on_decision(
        repeated.approval_id, "approve", expected_payload_sha256=repeated.payload_sha256, dispatch=dispatch
    )
    assert replayed.status is ApprovalStatus.REPLAYED
    assert replayed.content == executed.content
    assert len(calls) == 1
    assert audit.events == ["write_request", "confirm", "execute", "write_request", "replay"]


async def test_async_result_cache_failure_freezes_completed_side_effect(monkeypatch):
    monkeypatch.setattr(asyncio, "to_thread", _no_threads)

    class FailingExecutions(AsyncExecutions):
        async def put(self, *args, **kwargs):
            await asyncio.sleep(0)
            raise OSError("cache unavailable")

    approvals, audit = InMemoryPendingApprovalStore(), AsyncAudit()
    engine = DefaultApprovalEngine(approvals=approvals, executions=FailingExecutions(), audit=audit)
    pending = _pending("approval")
    await engine.on_request(pending)
    calls = []

    async def dispatch(name, arguments):
        calls.append(name)
        return ToolResult(outcome=ToolOutcome.COMPLETED, content="done")

    outcome = await engine.on_decision(
        pending.approval_id, "approve", expected_payload_sha256=pending.payload_sha256, dispatch=dispatch
    )
    assert outcome.status is ApprovalStatus.FROZEN
    assert calls == ["deploy"]
    assert (await approvals.get(pending.approval_id)).state == "execution_unknown"
    assert audit.events[-1] == "execute_unknown"


async def test_async_notification_mapping_is_awaited_before_create_or_patch(monkeypatch, gateway_client):
    monkeypatch.setattr(asyncio, "to_thread", _no_threads)

    class AsyncMessages:
        def __init__(self):
            self.messages = {}

        async def get(self, event_id):
            await asyncio.sleep(0)
            return self.messages.get(event_id)

        async def set(self, event_id, message_id):
            await asyncio.sleep(0)
            self.messages[event_id] = message_id

    store = AsyncMessages()
    first = await upsert_interactive_card(gateway_client, "event", {"status": "open"}, "chat", store=store)
    assert first.action == "created"
    assert store.messages == {"event": first.message_id}
    second = await upsert_interactive_card(gateway_client, "event", {"status": "done"}, "chat", store=store)
    assert second.action == "updated"
    assert len(gateway_client.im.send.calls) == 1
    assert gateway_client.im.patch.calls[0][0][0] == first.message_id


async def test_sync_sqlite_execution_and_jsonl_audit_remain_compatible(tmp_path):
    executions = SqliteExecutionResultStore(tmp_path / "executions.sqlite")
    try:
        audit_path = tmp_path / "audit.jsonl"
        engine = DefaultApprovalEngine(
            approvals=InMemoryPendingApprovalStore(), executions=executions, audit=JsonlAuditLog(audit_path)
        )
        pending = _pending("sync")
        await engine.on_request(pending)

        async def dispatch(name, arguments):
            return ToolResult(outcome=ToolOutcome.COMPLETED, content="persisted")

        result = await engine.on_decision(
            pending.approval_id, "approve", expected_payload_sha256=pending.payload_sha256, dispatch=dispatch
        )
        assert result.status is ApprovalStatus.EXECUTED
        assert [json.loads(line)["event"] for line in audit_path.read_text().splitlines()] == [
            "write_request",
            "confirm",
            "execute",
        ]
        assert (
            executions.get(next(iter(executions._db.execute("SELECT lookup_key FROM executions")))[0])["result"]
            == "persisted"
        )
    finally:
        executions._db.close()


async def test_store_invocation_awaits_awaitable_returned_by_sync_wrapper():
    from feishu._store_io import invoke_store

    main_thread = threading.get_ident()
    wrapper_threads = []

    def wrapped(value, *, suffix):
        wrapper_threads.append(threading.get_ident())

        async def resolve():
            assert threading.get_ident() == main_thread
            return value + suffix

        return resolve()

    assert await invoke_store(wrapped, "value", suffix="!") == "value!"
    assert wrapper_threads[0] != main_thread
