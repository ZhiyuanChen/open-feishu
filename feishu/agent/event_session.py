"""Atomically fence Agent session writes by their durable event lease."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .._sqlite import run_transaction
from ..events.jobs import EventJobContext, EventJobLeaseError, EventJobStatus
from .llm import Message
from .persistence import SqliteSessionStore, message_to_dict
from .scheduled_session import current_fenced_session

_CURRENT: ContextVar[tuple[Any, Any] | None] = ContextVar("fenced_event_session", default=None)


def validate_event_session(base: Any, context: EventJobContext) -> None:
    jobs = getattr(context.store, "_db", None)
    if not isinstance(base, SqliteSessionStore) or not isinstance(base._db, sqlite3.Connection):
        raise ValueError("protected Agent work requires a SQLite session store")
    if not isinstance(jobs, sqlite3.Connection):
        raise ValueError("protected Agent work requires a SQLite event store")
    try:
        left = base._db.execute("PRAGMA database_list").fetchone()[2]
        right = jobs.execute("PRAGMA database_list").fetchone()[2]
        if not left or not right or not os.path.samefile(left, right):
            raise ValueError("protected event and session stores must share one SQLite file")
    except (OSError, IndexError, TypeError) as exc:
        raise ValueError("protected event and session stores must share one SQLite file") from exc


class FencedEventSessionStore:
    def __init__(self, base: SqliteSessionStore, context: EventJobContext, session_id: str) -> None:
        validate_event_session(base, context)
        if not context.partition_key or context.partition_key != session_id:
            raise ValueError("event work must use its authoritative session partition")
        self.base, self.context, self.session_id = base, context, session_id

    def _check(self, session_id: str) -> None:
        row = self.base._db.execute(
            "SELECT 1 FROM event_jobs j JOIN event_job_control c ON c.event_id=j.event_id "
            "WHERE j.event_id=? AND j.status=? AND j.worker_id=? AND j.lease_until>? AND c.lease_token=?",
            (
                self.context.job_id,
                EventJobStatus.RUNNING.value,
                self.context.worker_id,
                self.context.clock(),
                self.context.lease_token,
            ),
        ).fetchone()
        if session_id != self.session_id or row is None:
            raise EventJobLeaseError("event session lease no longer owned")
        scheduled = current_fenced_session(self.base)
        if scheduled is not self.base:
            scheduled._check(session_id)

    async def get(self, session_id: str) -> list[Message]:
        return await self.base.get(session_id)

    async def updated_at(self, session_id: str) -> float | None:
        return await self.base.updated_at(session_id)

    def _write(self, session_id: str, data: list[dict[str, Any]]) -> None:
        if self.base._max_messages and len(data) > self.base._max_messages:
            data = data[-self.base._max_messages :]
        self.base._db.execute(
            "INSERT INTO sessions (session_id,messages,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET messages=excluded.messages,updated_at=excluded.updated_at",
            (session_id, json.dumps(data, ensure_ascii=False), time.time()),
        )

    async def append(self, session_id: str, *messages: Message) -> None:
        if not messages:
            return
        async with self.base._lock:

            def write() -> None:
                self._check(session_id)
                row = self.base._db.execute(
                    "SELECT messages FROM sessions WHERE session_id=?", (session_id,)
                ).fetchone()
                data = json.loads(row[0]) if row else []
                data.extend(message_to_dict(message) for message in messages)
                self._write(session_id, data)

            run_transaction(self.base._db, write)

    async def set(self, session_id: str, messages: list[Message]) -> None:
        data = [message_to_dict(message) for message in messages]
        async with self.base._lock:

            def write() -> None:
                self._check(session_id)
                self._write(session_id, data)

            run_transaction(self.base._db, write)

    async def clear(self, session_id: str) -> None:
        async with self.base._lock:

            def write() -> None:
                self._check(session_id)
                self.base._db.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))

            run_transaction(self.base._db, write)


def current_event_session(store: Any) -> Any:
    scoped = _CURRENT.get()
    return scoped[1] if scoped is not None and scoped[0] is store else store


@contextmanager
def fenced_event_session_scope(base: SqliteSessionStore, context: EventJobContext, session_id: str) -> Iterator[None]:
    token = _CURRENT.set((base, FencedEventSessionStore(base, context, session_id)))
    try:
        yield
    finally:
        _CURRENT.reset(token)
