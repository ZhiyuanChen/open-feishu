"""Cloudflare Durable Object SQLite adapters for OpenFeishu persistence contracts."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from feishu.agent.integrity import payload_summary
from feishu.agent.session import PendingApproval
from feishu.events.jobs import EventJob, SqliteEventJobStore

_ALARM_CLOCK_SKEW_SECONDS = 0.01


def _to_python(value: Any) -> Any:
    converter = getattr(value, "to_py", None)
    return converter() if callable(converter) else value


class CloudflareSqliteCursor:
    """Translate a Durable Object SQL cursor into the subset of DB-API used by the SDK."""

    def __init__(self, cursor: Any) -> None:
        rows = cursor.raw().toArray()
        self._rows = [tuple(_to_python(value) for value in row) for row in rows]
        self._index = 0
        written = int(getattr(cursor, "rowsWritten", 0) or 0)
        self.rows_written = max(0, written)
        self.rowcount = len(self._rows) if self._rows else self.rows_written

    def fetchone(self) -> tuple[Any, ...] | None:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows


class CloudflareSqliteConnection:
    """Expose Durable Object SQL as the synchronous SQLite connection subset used by SDK stores."""

    def __init__(self, storage: Any) -> None:
        self._storage = storage
        self._sql = getattr(storage, "sql", storage)
        self._transaction_depth = 0

    def execute(self, query: str, parameters: Sequence[Any] = ()) -> CloudflareSqliteCursor:
        if query.strip().upper().startswith(("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE")):
            raise RuntimeError("use run_transaction() with Durable Object SQL storage")
        cursor = CloudflareSqliteCursor(self._sql.exec(query, *parameters))
        statement = query.lstrip().split(maxsplit=1)[0].upper() if query.strip() else ""
        if statement in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            changes = CloudflareSqliteCursor(self._sql.exec("SELECT changes()")).fetchone()
            if changes is not None:
                cursor.rowcount = int(changes[0])
        return cursor

    def executemany(self, query: str, parameters: Iterable[Sequence[Any]]) -> CloudflareSqliteCursor:
        rows = list(parameters)

        def write_rows() -> CloudflareSqliteCursor:
            cursor: Any = _EmptyCursor()
            for row in rows:
                cursor = self.execute(query, row)
            return cursor

        return self.transaction_sync(write_rows)

    def transaction_sync(self, callback: Callable[[], Any]) -> Any:
        """Run a synchronous DB-API operation group in one Durable Object SQL transaction."""
        if self._transaction_depth:
            return callback()

        result: list[Any] = []

        def invoke() -> None:
            self._transaction_depth += 1
            try:
                result.append(callback())
            finally:
                self._transaction_depth -= 1

        transaction = self._storage.transactionSync
        try:
            from pyodide.ffi import create_proxy
        except ImportError:
            transaction(invoke)
        else:
            proxy = create_proxy(invoke)
            try:
                transaction(proxy)
            finally:
                proxy.destroy()
        return result[0] if result else None

    def commit(self) -> None:
        """Durable Object SQL commits each exec or transactionSync callback atomically."""

    def rollback(self) -> None:
        """Rollback is managed by transactionSync when its callback raises."""

    def close(self) -> None:
        """The Durable Object owns its storage for the full object lifetime."""

    def __enter__(self) -> CloudflareSqliteConnection:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


class CloudflareSeenStore:
    """TTL event deduplication in the Durable Object's SQLite database."""

    def __init__(
        self,
        connection: CloudflareSqliteConnection,
        *,
        ttl_seconds: float = 7 * 24 * 3600,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._db = connection
        self._ttl = ttl_seconds
        self._now = now
        self._db.execute("CREATE TABLE IF NOT EXISTS seen_events (event_id TEXT PRIMARY KEY, expires_at REAL NOT NULL)")

    async def seen(self, event_id: str) -> bool:
        now = self._now()
        self._db.execute("DELETE FROM seen_events WHERE expires_at <= ?", (now,))
        return self._db.execute("SELECT 1 FROM seen_events WHERE event_id = ?", (event_id,)).fetchone() is not None

    async def mark(self, event_id: str) -> None:
        now = self._now()
        self._db.execute("DELETE FROM seen_events WHERE expires_at <= ?", (now,))
        self._db.execute(
            "INSERT INTO seen_events (event_id, expires_at) VALUES (?, ?) "
            "ON CONFLICT(event_id) DO UPDATE SET expires_at = excluded.expires_at",
            (event_id, now + self._ttl),
        )

    async def claim(self, event_id: str) -> bool:
        now = self._now()
        self._db.execute("DELETE FROM seen_events WHERE expires_at <= ?", (now,))
        inserted = self._db.execute(
            "INSERT INTO seen_events (event_id, expires_at) VALUES (?, ?) "
            "ON CONFLICT(event_id) DO NOTHING RETURNING event_id",
            (event_id, now + self._ttl),
        ).fetchone()
        return inserted is not None


class CloudflareEventJobStore:
    """Durable Object inbox that wakes its alarm whenever work becomes eligible."""

    def __init__(self, connection: CloudflareSqliteConnection, storage: Any, *, max_attempts: int = 5) -> None:
        self._store = SqliteEventJobStore(":memory:", max_attempts=max_attempts, connection=connection)
        self._storage = storage

    async def enqueue(
        self,
        event_id: str,
        payload: dict[str, Any],
        *,
        partition_key: str,
        now: float | None = None,
    ) -> bool:
        inserted = await self._store.enqueue(event_id, payload, partition_key=partition_key, now=now)
        await self.schedule_next()
        return inserted

    async def claim_next(
        self,
        worker_id: str,
        *,
        now: float | None = None,
        lease_seconds: float = 60.0,
        availability_tolerance_seconds: float = 0.0,
    ) -> EventJob | None:
        return await self._store.claim_next(
            worker_id,
            now=now,
            lease_seconds=lease_seconds,
            availability_tolerance_seconds=max(availability_tolerance_seconds, _ALARM_CLOCK_SKEW_SECONDS),
        )

    async def renew(
        self, job_id: str, worker_id: str, *, now: float | None = None, lease_seconds: float = 60.0
    ) -> None:
        await self._store.renew(job_id, worker_id, now=now, lease_seconds=lease_seconds)

    async def mark_side_effect_started(self, job_id: str, worker_id: str) -> None:
        await self._store.mark_side_effect_started(job_id, worker_id)

    async def complete(self, job_id: str, worker_id: str, *, now: float | None = None) -> None:
        await self._store.complete(job_id, worker_id, now=now)

    async def retry(
        self,
        job_id: str,
        worker_id: str,
        *,
        available_at: float,
        error_type: str,
        now: float | None = None,
    ) -> None:
        await self._store.retry(job_id, worker_id, available_at=available_at, error_type=error_type, now=now)

    async def mark_unknown(self, job_id: str, worker_id: str, *, error_type: str, now: float | None = None) -> None:
        await self._store.mark_unknown(job_id, worker_id, error_type=error_type, now=now)

    async def requeue_unknown(self, job_id: str, *, now: float | None = None) -> None:
        await self._store.requeue_unknown(job_id, now=now)
        await self.schedule_next()

    async def requeue_failed(self, job_id: str, *, now: float | None = None) -> None:
        await self._store.requeue_failed(job_id, now=now)
        await self.schedule_next()

    async def get(self, job_id: str) -> EventJob | None:
        return await self._store.get(job_id)

    async def next_wakeup(self) -> float | None:
        return await self._store.next_wakeup()

    async def schedule_next(self) -> None:
        next_wakeup = await self.next_wakeup()
        if next_wakeup is not None:
            await self._storage.setAlarm(int(next_wakeup * 1000))


class CloudflareAuditLog:
    """Append-only approval audit records stored in Durable Object SQLite."""

    def __init__(self, connection: CloudflareSqliteConnection) -> None:
        self._db = connection
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS agent_audit ("
            "id INTEGER PRIMARY KEY, created_at REAL NOT NULL, event_type TEXT NOT NULL, "
            "key TEXT NOT NULL, outcome TEXT NOT NULL, error TEXT, event_id TEXT, message_id TEXT, "
            "tool_name TEXT, state TEXT, payload TEXT)"
        )

    def append(
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
        tool_name = approval.tool_name if approval is not None else None
        state = approval.state if approval is not None else None
        payload = (
            json.dumps(payload_summary(approval.arguments, include_hash=True), ensure_ascii=False) if approval else None
        )
        self._db.execute(
            "INSERT INTO agent_audit "
            "(created_at, event_type, key, outcome, error, event_id, message_id, tool_name, state, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), event_type, key, outcome, error, event_id, message_id, tool_name, state, payload),
        )


class _EmptyCursor:
    rowcount = 0

    def raw(self) -> _EmptyCursor:
        return self

    def toArray(self) -> list[tuple[Any, ...]]:
        return []
