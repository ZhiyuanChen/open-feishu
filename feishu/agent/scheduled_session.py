"""Atomic lease-fenced SQLite session writes for scheduled Agent runs."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .._sqlite import run_transaction
from ..auth import user_identity_keys
from .llm import Message
from .persistence import SqliteSessionStore, message_to_dict
from .replies import ScheduledTurn
from .scheduled import ScheduledTaskStore

_CURRENT: ContextVar[tuple[Any, FencedScheduledSessionStore] | None] = ContextVar(
    "scheduled_session_store", default=None
)


def validate_fenced_stores(store: Any, scheduled_store: ScheduledTaskStore) -> None:
    """Require one SQLite file so fence validation and writes share a transaction."""
    if not isinstance(store, SqliteSessionStore) or not isinstance(store._db, sqlite3.Connection):
        raise ValueError("scheduled tasks require an atomically fenced SQLite session store")
    row = store._db.execute("PRAGMA database_list").fetchone()
    actual = row[2] if row else ""
    try:
        same_file = bool(actual) and os.path.samefile(actual, scheduled_store.path)
    except OSError:
        same_file = False
    if not same_file:
        raise ValueError("scheduled and session stores must use the same SQLite database")


class FencedScheduledSessionStore:
    """A scoped session view which atomically validates the run lease on each write."""

    def __init__(
        self,
        base: SqliteSessionStore,
        scheduled_store: ScheduledTaskStore,
        turn: ScheduledTurn,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        validate_fenced_stores(base, scheduled_store)
        if not isinstance(turn, ScheduledTurn):
            raise ValueError("a scheduled turn is required")
        self.base, self.turn, self.clock = base, turn, clock

    def _check(self, session_id: str) -> None:
        if session_id != self.turn.session_id or not self.turn.lease_token:
            raise PermissionError("scheduled session or lease mismatch")
        row = self.base._db.execute(
            "SELECT t.owner_keys FROM scheduled_runs r JOIN scheduled_tasks t ON t.task_id=r.task_id "
            "WHERE r.run_id=? AND r.session_id=? AND r.status='running' AND r.lease_token=? AND r.lease_until>? "
            "AND t.tenant_key=? AND t.chat_id=? AND t.policy=?",
            (
                self.turn.run_id,
                session_id,
                self.turn.lease_token,
                self.clock(),
                self.turn.tenant_key,
                self.turn.chat_id,
                self.turn.policy,
            ),
        ).fetchone()
        if row is None or set(json.loads(row[0])) != set(user_identity_keys(self.turn.user)):
            raise PermissionError("scheduled session lease is not active")

    async def get(self, session_id: str) -> list[Message]:
        return await self.base.get(session_id)

    async def updated_at(self, session_id: str) -> float | None:
        return await self.base.updated_at(session_id)

    def _write(self, session_id: str, data: list[dict[str, Any]]) -> None:
        if self.base._max_messages and len(data) > self.base._max_messages:
            data = data[-self.base._max_messages :]
        self.base._db.execute(
            "INSERT INTO sessions (session_id, messages, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET messages=excluded.messages, updated_at=excluded.updated_at",
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


def current_fenced_session(store: Any) -> Any:
    scoped = _CURRENT.get()
    return scoped[1] if scoped is not None and scoped[0] is store else store


@contextmanager
def fenced_session_scope(
    base: SqliteSessionStore,
    scheduled_store: ScheduledTaskStore,
    turn: ScheduledTurn,
    *,
    clock: Callable[[], float] = time.time,
) -> Iterator[None]:
    view = FencedScheduledSessionStore(base, scheduled_store, turn, clock=clock)
    token = _CURRENT.set((base, view))
    try:
        yield
    finally:
        _CURRENT.reset(token)
