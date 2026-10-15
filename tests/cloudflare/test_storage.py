from __future__ import annotations

import sqlite3
from importlib import import_module
from typing import Any

import pytest
from starlette.testclient import TestClient

from feishu._sqlite import run_transaction
from feishu.agent.llm import Message, TextPart
from feishu.agent.persistence import (
    SqliteExecutionResultStore,
    SqlitePendingApprovalStore,
    SqlitePendingAuthorizationStore,
    SqliteSessionStore,
)
from feishu.agent.session import ClaimResult, PendingApproval, PendingAuthorization
from feishu.auth.user_tokens import SqliteOAuthTokenStore
from feishu.events.job_runner import EventJobRunner
from feishu.events.jobs import EventJobStatus, SqliteEventJobStore
from tests.conftest import signed_event

_cloudflare_storage = import_module("examples.cloudflare.src.storage")
CloudflareAuditLog = _cloudflare_storage.CloudflareAuditLog
CloudflareEventJobStore = _cloudflare_storage.CloudflareEventJobStore
CloudflareSeenStore = _cloudflare_storage.CloudflareSeenStore
CloudflareSqliteConnection = _cloudflare_storage.CloudflareSqliteConnection
create_runtime_agent = import_module("examples.cloudflare.src.agent_app").create_runtime_agent


class _RawCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def toArray(self) -> list[tuple[Any, ...]]:
        return self._rows


class _SqlCursor:
    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._rows = cursor.fetchall() if cursor.description else []
        self.rowsWritten = max(0, cursor.rowcount)

    def raw(self) -> _RawCursor:
        return _RawCursor(self._rows)


class _ZeroRowsWrittenSqlCursor(_SqlCursor):
    def __init__(self, cursor: sqlite3.Cursor) -> None:
        super().__init__(cursor)
        self.rowsWritten = 0


class _UnknownRowsWrittenSqlCursor(_SqlCursor):
    def __init__(self, cursor: sqlite3.Cursor) -> None:
        super().__init__(cursor)
        self.rowsWritten = -1


class _FakeSqlStorage:
    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self.sql = self
        self.transactions = 0

    def exec(self, query: str, *bindings: Any) -> _SqlCursor:
        cursor = self.connection.execute(query, bindings)
        return _SqlCursor(cursor)

    def transactionSync(self, callback):
        self.transactions += 1
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            result = callback()
        except BaseException:
            self.connection.rollback()
            raise
        self.connection.commit()
        return result


class _ZeroRowsWrittenSqlStorage(_FakeSqlStorage):
    def exec(self, query: str, *bindings: Any) -> _ZeroRowsWrittenSqlCursor:
        return _ZeroRowsWrittenSqlCursor(self.connection.execute(query, bindings))


class _UnknownRowsWrittenSqlStorage(_FakeSqlStorage):
    def exec(self, query: str, *bindings: Any) -> _UnknownRowsWrittenSqlCursor:
        return _UnknownRowsWrittenSqlCursor(self.connection.execute(query, bindings))


class _InflatedRowsWrittenSqlStorage(_FakeSqlStorage):
    def exec(self, query: str, *bindings: Any) -> _SqlCursor:
        cursor = _SqlCursor(self.connection.execute(query, bindings))
        statement = query.lstrip().split(maxsplit=1)[0].upper()
        if statement == "INSERT":
            cursor.rowsWritten = 3
        elif statement == "UPDATE":
            cursor.rowsWritten = 2
        return cursor


def test_sqlite_connection_reads_and_writes_durable_object_sql_rows() -> None:
    connection = CloudflareSqliteConnection(_FakeSqlStorage())
    connection.execute("CREATE TABLE sample (id TEXT PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO sample (id, value) VALUES (?, ?)", ("k", "v"))

    row = connection.execute("SELECT id, value FROM sample WHERE id = ?", ("k",)).fetchone()

    assert row == ("k", "v")


def test_sqlite_connection_preserves_durable_object_rows_written_count() -> None:
    connection = CloudflareSqliteConnection(_FakeSqlStorage())
    connection.execute("CREATE TABLE sample (id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO sample (id) VALUES (?)", ("a",))
    connection.execute("INSERT INTO sample (id) VALUES (?)", ("b",))

    deleted = connection.execute("DELETE FROM sample")

    assert deleted.rowcount == 2


def test_sqlite_connection_maps_explicit_transactions_and_rolls_back() -> None:
    connection = CloudflareSqliteConnection(_FakeSqlStorage())
    connection.execute("CREATE TABLE sample (id TEXT PRIMARY KEY)")

    def failing_write() -> None:
        connection.execute("INSERT INTO sample (id) VALUES (?)", ("rolled-back",))
        raise RuntimeError("abort transaction")

    with pytest.raises(RuntimeError, match="abort transaction"):
        run_transaction(connection, failing_write)

    assert connection.execute("SELECT id FROM sample").fetchall() == []


async def test_cloudflare_event_job_claim_uses_sqlite_changes_when_rows_written_is_zero() -> None:
    connection = CloudflareSqliteConnection(_ZeroRowsWrittenSqlStorage())
    jobs = SqliteEventJobStore(":memory:", connection=connection)

    assert await jobs.enqueue("evt-changes", {}, partition_key="chat-1", now=10)
    claimed = await jobs.claim_next("worker-a", now=11)

    assert claimed is not None and claimed.event_id == "evt-changes"
    assert claimed.status is EventJobStatus.RUNNING


async def test_cloudflare_event_job_claim_uses_sqlite_changes_when_rows_written_is_unknown() -> None:
    connection = CloudflareSqliteConnection(_UnknownRowsWrittenSqlStorage())
    jobs = SqliteEventJobStore(":memory:", connection=connection)

    assert await jobs.enqueue("evt-changes-unknown", {}, partition_key="chat-1", now=10)
    claimed = await jobs.claim_next("worker-a", now=11)

    assert claimed is not None and claimed.event_id == "evt-changes-unknown"
    assert claimed.status is EventJobStatus.RUNNING


async def test_cloudflare_event_job_claim_uses_sqlite_changes_when_rows_written_is_inflated() -> None:
    connection = CloudflareSqliteConnection(_InflatedRowsWrittenSqlStorage())
    jobs = SqliteEventJobStore(":memory:", connection=connection)

    assert await jobs.enqueue("evt-changes-inflated", {}, partition_key="chat-1", now=10)
    claimed = await jobs.claim_next("worker-a", now=11)

    assert claimed is not None and claimed.event_id == "evt-changes-inflated"
    assert claimed.status is EventJobStatus.RUNNING


async def test_existing_sqlite_session_store_can_use_cloudflare_connection() -> None:
    connection = CloudflareSqliteConnection(_FakeSqlStorage())
    first = SqliteSessionStore(":memory:", connection=connection)
    await first.append("chat", Message(role="user", content=[TextPart(text="hello")]))

    second = SqliteSessionStore(":memory:", connection=connection)

    history = await second.get("chat")
    assert len(history) == 1
    assert history[0].content == [TextPart(text="hello")]


async def test_cloudflare_seen_store_claim_is_atomic_and_persistent() -> None:
    storage = _FakeSqlStorage()
    connection = CloudflareSqliteConnection(storage)
    first = CloudflareSeenStore(connection, ttl_seconds=600, now=lambda: 10)

    assert await first.claim("evt-1") is True
    assert await first.claim("evt-1") is False

    second = CloudflareSeenStore(connection, ttl_seconds=600, now=lambda: 10)
    assert await second.seen("evt-1") is True


async def test_sqlite_event_jobs_run_over_cloudflare_sql_connection() -> None:
    connection = CloudflareSqliteConnection(_FakeSqlStorage())
    jobs = SqliteEventJobStore(":memory:", connection=connection)

    assert await jobs.enqueue("evt-1", {"header": {"event_id": "evt-1"}}, partition_key="chat-1", now=1)
    claimed = await jobs.claim_next("worker-a", now=2, lease_seconds=10)

    assert claimed is not None and claimed.status is EventJobStatus.RUNNING
    assert claimed.payload == {"header": {"event_id": "evt-1"}}


async def test_cloudflare_event_job_enqueue_schedules_alarm() -> None:
    class AlarmStorage:
        def __init__(self) -> None:
            self.alarms: list[int] = []

        async def setAlarm(self, timestamp: int) -> None:
            self.alarms.append(timestamp)

    alarm_storage = AlarmStorage()
    jobs = CloudflareEventJobStore(CloudflareSqliteConnection(_FakeSqlStorage()), alarm_storage)

    await jobs.enqueue("evt-1", {}, partition_key="chat-1", now=10)

    assert alarm_storage.alarms == [10_000]


async def test_cloudflare_event_job_claim_tolerates_alarm_clock_rounding() -> None:
    class AlarmStorage:
        async def setAlarm(self, _timestamp: int) -> None:
            return None

    jobs = CloudflareEventJobStore(CloudflareSqliteConnection(_FakeSqlStorage()), AlarmStorage())
    await jobs.enqueue("evt-rounding", {}, partition_key="chat-1", now=10.0)

    claimed = await jobs.claim_next("worker-a", now=9.995)

    assert claimed is not None and claimed.event_id == "evt-rounding"


async def test_agent_store_updates_use_durable_object_transaction() -> None:
    storage = _FakeSqlStorage()
    connection = CloudflareSqliteConnection(storage)
    approvals = SqlitePendingApprovalStore(":memory:", connection=connection)
    await approvals.put(PendingApproval("ap-1", "chat-1", "call-1", "write", {"x": 1}))

    def fail_update(_approval):
        raise RuntimeError("abort update")

    with pytest.raises(RuntimeError, match="abort update"):
        await approvals.update("ap-1", fail_update)

    current = await approvals.get("ap-1")
    assert current is not None and current.state == "awaiting_confirmation"
    assert storage.transactions == 1


async def test_cloudflare_connection_persists_agent_approval_authorization_oauth_and_cache_stores() -> None:
    connection = CloudflareSqliteConnection(_FakeSqlStorage())
    approvals = SqlitePendingApprovalStore(":memory:", connection=connection)
    authorizations = SqlitePendingAuthorizationStore(":memory:", connection=connection)
    executions = SqliteExecutionResultStore(":memory:", connection=connection)
    oauth = SqliteOAuthTokenStore(":memory:", connection=connection)

    approval = PendingApproval("ap-1", "chat-1", "call-1", "write", {"x": 1}, payload_sha256="hash")
    authorization = PendingAuthorization("auth-1", "chat-1", "call-2", "read", {"x": 2})
    await approvals.put(approval)
    await authorizations.put(authorization)

    assert await approvals.claim("ap-1", expected_payload_sha256="hash") is ClaimResult.CLAIMED
    assert await authorizations.claim("auth-1") is ClaimResult.CLAIMED
    executions.put("idem-1", execution_status="executed", result={"ok": True})
    execution = executions.get("idem-1")
    assert execution is not None and execution["result"] == {"ok": True}

    await oauth.save({"access_token": "user-token", "expires_in": 3600}, user_info={"open_id": "ou-1"})
    token = await oauth.get({"open_id": "ou-1"})
    assert token is not None and token.user_access_token == "user-token"

    audit = CloudflareAuditLog(connection)
    audit.append("execute", key="ap-1", approval=approval)
    row = connection.execute("SELECT event_type, key FROM agent_audit").fetchone()
    assert row == ("execute", "ap-1")


async def test_local_smoke_agent_hands_off_then_processes_a_signed_event() -> None:
    class AlarmStorage:
        async def setAlarm(self, _timestamp: int) -> None:
            return None

    storage = _FakeSqlStorage()
    connection = CloudflareSqliteConnection(storage)
    jobs = CloudflareEventJobStore(connection, AlarmStorage())
    seen_store = CloudflareSeenStore(connection)
    env = {
        "AGENT_LOCAL_SMOKE": "1",
        "FEISHU_ENCRYPT_KEY": "cf-encrypt",
        "FEISHU_VERIFICATION_TOKEN": "cf-smoke",
    }
    agent = create_runtime_agent(env, connection=connection, jobs=jobs, seen_store=seen_store)
    payload = {
        "schema": "2.0",
        "header": {
            "event_id": "evt-smoke",
            "event_type": "im.message.receive_v1",
            "token": "cf-smoke",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou-smoke"}, "sender_type": "user"},
            "message": {
                "chat_id": "oc-smoke",
                "message_id": "om-smoke",
                "message_type": "text",
                "content": '{"text":"hello"}',
            },
        },
    }
    raw, headers = signed_event(payload, encrypt_key="cf-encrypt")

    with TestClient(agent.asgi_app()) as client:
        response = client.post("/feishu/event", content=raw, headers=headers)
        assert response.status_code == 200

    runner = EventJobRunner(jobs, agent.handle_event, worker_id="local-alarm")
    completed = await runner.run_once()

    assert completed is not None and completed.status is EventJobStatus.COMPLETED
    history = await agent.engine.store.get("oc-smoke")
    assert len(history) == 2
    assert history[0].source_event_id == "evt-smoke"
