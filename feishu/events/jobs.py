# OpenFeishu
# Copyright (C) 2024-Present  DanLing
#
# This file is part of OpenFeishu.
#
# OpenFeishu is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# OpenFeishu is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Durable inbox jobs for webhook events."""

from __future__ import annotations

import asyncio
import contextvars
import copy
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .._sqlite import connect, run_transaction

_COMPLETED_TTL_SECONDS = 7 * 24 * 3600
_UNKNOWN_TTL_SECONDS = 30 * 24 * 3600


class EventJobStatus(str, Enum):
    """Durable event inbox lifecycle states."""

    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class EventJob:
    """A persisted Feishu event waiting for or undergoing processing."""

    job_id: str
    event_id: str
    payload: dict[str, Any]
    partition_key: str
    status: EventJobStatus
    attempt: int
    created_at: float
    updated_at: float
    available_at: float
    worker_id: str | None = None
    lease_until: float | None = None
    side_effect_started: bool = False
    error_type: str | None = None
    lease_token: str | None = None
    private_message: bool = False


class EventJobStoreError(RuntimeError):
    """Base error for an invalid durable event-job transition."""


class EventJobLeaseError(EventJobStoreError):
    """Raised when a worker no longer owns a running job lease."""


@dataclass(slots=True)
class EventJobContext:
    """Context-local lease used by handlers to record their first external side effect."""

    store: EventJobStore
    job_id: str
    event_id: str
    worker_id: str
    side_effect_started: bool = False
    lease_token: str | None = None
    partition_key: str | None = None
    interruptible: bool = False
    cancellation: asyncio.Event | None = None
    clock: Callable[[], float] = time.time

    async def mark_side_effect_started(self) -> None:
        if self.lease_token:
            await self.store.mark_fenced_started(  # type: ignore[attr-defined]
                self.job_id, self.worker_id, self.lease_token, now=self.clock()
            )
            self.side_effect_started = True
            return
        if self.side_effect_started:
            return
        await self.store.mark_side_effect_started(self.job_id, self.worker_id)
        self.side_effect_started = True


_CURRENT_EVENT_JOB: contextvars.ContextVar[EventJobContext | None] = contextvars.ContextVar(
    "feishu_current_event_job", default=None
)


def current_event_job() -> EventJobContext | None:
    """Return the job context for the current task, if processing a durable event."""
    return _CURRENT_EVENT_JOB.get()


async def mark_current_event_side_effect_started() -> None:
    """Mark the current durable event before making an external side effect."""
    context = current_event_job()
    if context is not None:
        await context.mark_side_effect_started()


class EventSuperseded(BaseException):
    """A newer direct message superseded this event at a safe checkpoint.

    This is deliberately separate from CancelledError: Python 3.10 discards
    CancelledError subclasses when their task is awaited by another task.
    """


@asynccontextmanager
async def current_event_effect() -> AsyncIterator[None]:
    """Persist the lifecycle of one external effect for a protected event."""
    context = current_event_job()
    if context is None or not context.lease_token:
        yield
        return
    await context.store.begin_effect(  # type: ignore[attr-defined]
        context.job_id, context.worker_id, context.lease_token, now=context.clock()
    )
    context.side_effect_started = True
    try:
        yield
    except BaseException:
        await asyncio.shield(
            context.store.end_effect(  # type: ignore[attr-defined]
                context.job_id, context.worker_id, context.lease_token, uncertain=True, now=context.clock()
            )
        )
        raise
    else:
        await context.store.end_effect(  # type: ignore[attr-defined]
            context.job_id, context.worker_id, context.lease_token, uncertain=False, now=context.clock()
        )


def event_checkpoint() -> None:
    """Stop an interruptible turn only between completed operations."""
    context = current_event_job()
    if (
        context is not None
        and context.interruptible
        and context.cancellation is not None
        and context.cancellation.is_set()
    ):
        raise EventSuperseded()


@runtime_checkable
class EventJobStore(Protocol):
    """Persistence contract for idempotent, partition-ordered event work."""

    async def enqueue(
        self,
        event_id: str,
        payload: dict[str, Any],
        *,
        partition_key: str,
        now: float | None = None,
    ) -> bool:
        """Persist a new event once; return ``False`` for a duplicate ID."""
        ...

    async def claim_next(
        self,
        worker_id: str,
        *,
        now: float | None = None,
        lease_seconds: float = 60.0,
        availability_tolerance_seconds: float = 0.0,
    ) -> EventJob | None:
        """Claim the oldest eligible job without overlapping its partition."""
        ...

    async def renew(
        self, job_id: str, worker_id: str, *, now: float | None = None, lease_seconds: float = 60.0
    ) -> None:
        """Extend a job lease still owned by ``worker_id``."""
        ...

    async def mark_side_effect_started(self, job_id: str, worker_id: str) -> None:
        """Persist the fail-closed boundary before a non-transactional external effect."""
        ...

    async def complete(self, job_id: str, worker_id: str, *, now: float | None = None) -> None:
        """Mark a successfully processed job complete."""
        ...

    async def retry(
        self, job_id: str, worker_id: str, *, available_at: float, error_type: str, now: float | None = None
    ) -> None:
        """Return a pre-effect failure to the queue, or fail it after the attempt limit."""
        ...

    async def mark_unknown(self, job_id: str, worker_id: str, *, error_type: str, now: float | None = None) -> None:
        """Freeze a job whose external side-effect outcome is uncertain."""
        ...

    async def requeue_unknown(self, job_id: str, *, now: float | None = None) -> None:
        """Explicitly requeue an unknown job after an operator reconciles its side effect."""
        ...

    async def requeue_failed(self, job_id: str, *, now: float | None = None) -> None:
        """Explicitly reset the retry budget for a job that exhausted safe retries."""
        ...

    async def get(self, job_id: str) -> EventJob | None:
        """Read a job without changing its state."""
        ...

    async def next_wakeup(self) -> float | None:
        """Return the next queued availability or lease expiry, if any."""
        ...


class InMemoryEventJobStore:
    """In-memory event jobs for tests and single-process examples."""

    def __init__(self, *, max_attempts: int = 5) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._max_attempts = max_attempts
        self._jobs: dict[str, EventJob] = {}
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        event_id: str,
        payload: dict[str, Any],
        *,
        partition_key: str,
        now: float | None = None,
    ) -> bool:
        created = time.time() if now is None else now
        async with self._lock:
            self._purge(created)
            if event_id in self._jobs:
                return False
            self._jobs[event_id] = EventJob(
                job_id=event_id,
                event_id=event_id,
                payload=copy.deepcopy(payload),
                partition_key=partition_key,
                status=EventJobStatus.QUEUED,
                attempt=0,
                created_at=created,
                updated_at=created,
                available_at=created,
            )
            return True

    async def claim_next(
        self,
        worker_id: str,
        *,
        now: float | None = None,
        lease_seconds: float = 60.0,
        availability_tolerance_seconds: float = 0.0,
    ) -> EventJob | None:
        current = time.time() if now is None else now
        if not worker_id:
            raise ValueError("worker_id must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if availability_tolerance_seconds < 0:
            raise ValueError("availability_tolerance_seconds must be non-negative")
        async with self._lock:
            self._purge(current)
            self._recover_expired(current)
            first_unresolved: dict[str, EventJob] = {}
            for job in self._jobs.values():
                if job.status is EventJobStatus.COMPLETED:
                    continue
                # Insertion order remains stable even when the wall clock moves backwards.
                first_unresolved.setdefault(job.partition_key, job)
            candidates = [
                job
                for job in self._jobs.values()
                if job.status in (EventJobStatus.QUEUED, EventJobStatus.RETRY_WAIT)
                and job.available_at <= current + availability_tolerance_seconds
                and first_unresolved[job.partition_key].job_id == job.job_id
            ]
            if not candidates:
                return None
            job = candidates[0]
            job.status = EventJobStatus.RUNNING
            job.attempt += 1
            job.worker_id = worker_id
            job.lease_until = current + lease_seconds
            job.updated_at = current
            job.error_type = None
            return _copy_job(job)

    async def renew(
        self, job_id: str, worker_id: str, *, now: float | None = None, lease_seconds: float = 60.0
    ) -> None:
        current = time.time() if now is None else now
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        async with self._lock:
            job = self._owned(job_id, worker_id)
            job.lease_until = current + lease_seconds
            job.updated_at = current

    async def mark_side_effect_started(self, job_id: str, worker_id: str) -> None:
        async with self._lock:
            self._owned(job_id, worker_id).side_effect_started = True

    async def complete(self, job_id: str, worker_id: str, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        async with self._lock:
            job = self._owned(job_id, worker_id)
            job.status = EventJobStatus.COMPLETED
            job.worker_id = None
            job.lease_until = None
            job.updated_at = current

    async def retry(
        self, job_id: str, worker_id: str, *, available_at: float, error_type: str, now: float | None = None
    ) -> None:
        current = time.time() if now is None else now
        async with self._lock:
            job = self._owned(job_id, worker_id)
            if job.side_effect_started:
                raise EventJobStoreError("cannot automatically retry after a side effect started")
            job.status = EventJobStatus.RETRY_WAIT if job.attempt < self._max_attempts else EventJobStatus.FAILED
            job.available_at = available_at
            job.worker_id = None
            job.lease_until = None
            job.updated_at = current
            job.error_type = error_type

    async def mark_unknown(self, job_id: str, worker_id: str, *, error_type: str, now: float | None = None) -> None:
        current = time.time() if now is None else now
        async with self._lock:
            job = self._owned(job_id, worker_id)
            job.status = EventJobStatus.UNKNOWN
            job.worker_id = None
            job.lease_until = None
            job.updated_at = current
            job.error_type = error_type

    async def requeue_unknown(self, job_id: str, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status is not EventJobStatus.UNKNOWN:
                raise EventJobStoreError(f"job {job_id!r} is not unknown")
            job.status = EventJobStatus.QUEUED
            job.available_at = current
            job.attempt = 0
            job.side_effect_started = False
            job.updated_at = current
            job.error_type = None

    async def requeue_failed(self, job_id: str, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status is not EventJobStatus.FAILED:
                raise EventJobStoreError(f"job {job_id!r} is not failed")
            job.status = EventJobStatus.QUEUED
            job.attempt = 0
            job.available_at = current
            job.updated_at = current
            job.error_type = None

    async def get(self, job_id: str) -> EventJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            return _copy_job(job) if job is not None else None

    async def next_wakeup(self) -> float | None:
        async with self._lock:
            first_unresolved: dict[str, EventJob] = {}
            for job in self._jobs.values():
                if job.status is EventJobStatus.COMPLETED:
                    continue
                first_unresolved.setdefault(job.partition_key, job)
            times = []
            for job in first_unresolved.values():
                if job.status in (EventJobStatus.QUEUED, EventJobStatus.RETRY_WAIT):
                    times.append(job.available_at)
                elif job.status is EventJobStatus.RUNNING and job.lease_until is not None:
                    times.append(job.lease_until)
            return min(times) if times else None

    def _owned(self, job_id: str, worker_id: str) -> EventJob:
        job = self._jobs.get(job_id)
        if job is None or job.status is not EventJobStatus.RUNNING or job.worker_id != worker_id:
            raise EventJobLeaseError(f"worker {worker_id!r} does not own a running lease for job {job_id!r}")
        return job

    def _recover_expired(self, now: float) -> None:
        for job in self._jobs.values():
            if job.status is not EventJobStatus.RUNNING or job.lease_until is None or job.lease_until > now:
                continue
            job.worker_id = None
            job.lease_until = None
            job.updated_at = now
            if job.side_effect_started:
                job.status = EventJobStatus.UNKNOWN
                job.error_type = "LeaseExpiredAfterSideEffect"
            else:
                job.status = EventJobStatus.RETRY_WAIT if job.attempt < self._max_attempts else EventJobStatus.FAILED
                job.available_at = now
                job.error_type = "LeaseExpired"

    def _purge(self, now: float) -> None:
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if (job.status is EventJobStatus.COMPLETED and job.updated_at + _COMPLETED_TTL_SECONDS <= now)
            or (job.status is EventJobStatus.UNKNOWN and job.updated_at + _UNKNOWN_TTL_SECONDS <= now)
        ]
        for job_id in expired:
            del self._jobs[job_id]


class SqliteEventJobStore:
    """SQLite-backed durable event inbox for resident runtimes.

    Receipt ordering is backfilled when opening a legacy database. Stop all
    older writers and workers before upgrading; mixed versions cannot preserve
    receipt ordering and older writers do not populate the ordering table.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_attempts: int = 5,
        completed_ttl_seconds: int = _COMPLETED_TTL_SECONDS,
        unknown_ttl_seconds: int | None = _UNKNOWN_TTL_SECONDS,
        connection: Any | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._db: Any = connection if connection is not None else connect(path)
        self._max_attempts = max_attempts
        self._completed_ttl = completed_ttl_seconds
        self._unknown_ttl = unknown_ttl_seconds
        self._lock = asyncio.Lock()
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS event_jobs ("
            "event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, partition_key TEXT NOT NULL, "
            "status TEXT NOT NULL, attempt INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, "
            "available_at REAL NOT NULL, worker_id TEXT, lease_until REAL, "
            "side_effect_started INTEGER NOT NULL DEFAULT 0, "
            "error_type TEXT)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS event_jobs_claim "
            "ON event_jobs(status, available_at, created_at, partition_key)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS event_job_order ("
            "sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS event_job_control ("
            "event_id TEXT PRIMARY KEY, private_message INTEGER NOT NULL DEFAULT 0, "
            "lease_token TEXT, inflight INTEGER NOT NULL DEFAULT 0, uncertain INTEGER NOT NULL DEFAULT 0)"
        )
        self._db.commit()
        run_transaction(self._db, self._sync_receipt_order)

    async def enqueue(
        self,
        event_id: str,
        payload: dict[str, Any],
        *,
        partition_key: str,
        now: float | None = None,
        private_message: bool = False,
    ) -> bool:
        current = time.time() if now is None else now
        if private_message and self._unknown_ttl is not None:
            raise ValueError("private interruption requires UNKNOWN records without automatic expiry")
        async with self._lock:
            return run_transaction(
                self._db,
                lambda: self._enqueue_in_transaction(
                    event_id, payload, partition_key=partition_key, now=current, private_message=private_message
                ),
            )

    async def should_interrupt(self, job_id: str, worker_id: str, lease_token: str, *, now: float) -> bool:
        """Check the first later work item without skipping a callback."""
        async with self._lock:
            row = self._db.execute(
                "SELECT j.partition_key,o.sequence,c.private_message FROM event_jobs j "
                "JOIN event_job_order o ON o.event_id=j.event_id "
                "JOIN event_job_control c ON c.event_id=j.event_id "
                "WHERE j.event_id=? AND j.status=? AND j.worker_id=? AND j.lease_until>? AND c.lease_token=?",
                (job_id, EventJobStatus.RUNNING.value, worker_id, now, lease_token),
            ).fetchone()
            if row is None:
                raise EventJobLeaseError("event lease no longer owned")
            if not row[2]:
                return False
            successor = self._db.execute(
                "SELECT c.private_message FROM event_jobs j JOIN event_job_order o ON o.event_id=j.event_id "
                "LEFT JOIN event_job_control c ON c.event_id=j.event_id "
                "WHERE j.partition_key=? AND o.sequence>? AND j.status!=? ORDER BY o.sequence LIMIT 1",
                (row[0], row[1], EventJobStatus.COMPLETED.value),
            ).fetchone()
            return bool(successor and successor[0])

    async def validate_lease(self, job_id: str, worker_id: str, lease_token: str, *, now: float) -> None:
        async with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM event_jobs j JOIN event_job_control c ON c.event_id=j.event_id "
                "WHERE j.event_id=? AND j.status=? AND j.worker_id=? AND j.lease_until>? AND c.lease_token=?",
                (job_id, EventJobStatus.RUNNING.value, worker_id, now, lease_token),
            ).fetchone()
            if row is None:
                raise EventJobLeaseError("event lease no longer owned")

    async def begin_effect(self, job_id: str, worker_id: str, lease_token: str, *, now: float) -> None:
        async with self._lock:

            def begin() -> None:
                cursor = self._db.execute(
                    "UPDATE event_job_control SET inflight=inflight+1 WHERE event_id=? AND lease_token=? "
                    "AND EXISTS (SELECT 1 FROM event_jobs WHERE event_id=? AND status=? "
                    "AND worker_id=? AND lease_until>?)",
                    (job_id, lease_token, job_id, EventJobStatus.RUNNING.value, worker_id, now),
                )
                self._require_owner(cursor.rowcount, job_id, worker_id)
                self._db.execute("UPDATE event_jobs SET side_effect_started=1 WHERE event_id=?", (job_id,))

            run_transaction(self._db, begin)

    async def mark_fenced_started(self, job_id: str, worker_id: str, lease_token: str, *, now: float) -> None:
        async with self._lock:
            cursor = self._db.execute(
                "UPDATE event_jobs SET side_effect_started=1 WHERE event_id=? AND status=? AND worker_id=? "
                "AND lease_until>? AND EXISTS (SELECT 1 FROM event_job_control WHERE event_id=? AND lease_token=?)",
                (job_id, EventJobStatus.RUNNING.value, worker_id, now, job_id, lease_token),
            )
            self._db.commit()
            self._require_owner(cursor.rowcount, job_id, worker_id)

    async def end_effect(self, job_id: str, worker_id: str, lease_token: str, *, uncertain: bool, now: float) -> None:
        async with self._lock:
            cursor = self._db.execute(
                "UPDATE event_job_control SET inflight=inflight-1,uncertain=MAX(uncertain,?) "
                "WHERE event_id=? AND lease_token=? AND inflight>0 AND EXISTS "
                "(SELECT 1 FROM event_jobs WHERE event_id=? AND status=? AND worker_id=? AND lease_until>?)",
                (int(uncertain), job_id, lease_token, job_id, EventJobStatus.RUNNING.value, worker_id, now),
            )
            self._db.commit()
            self._require_owner(cursor.rowcount, job_id, worker_id)

    async def renew_fenced(
        self, job_id: str, worker_id: str, lease_token: str, *, now: float, lease_seconds: float
    ) -> None:
        async with self._lock:
            cursor = self._db.execute(
                "UPDATE event_jobs SET lease_until=?,updated_at=? WHERE event_id=? AND status=? AND worker_id=? "
                "AND lease_until>? AND EXISTS (SELECT 1 FROM event_job_control WHERE event_id=? AND lease_token=?)",
                (now + lease_seconds, now, job_id, EventJobStatus.RUNNING.value, worker_id, now, job_id, lease_token),
            )
            self._db.commit()
            self._require_owner(cursor.rowcount, job_id, worker_id)

    async def finish_fenced(
        self, job_id: str, worker_id: str, lease_token: str, *, now: float, superseded: bool = False
    ) -> None:
        async with self._lock:

            def finish() -> None:
                row = self._db.execute(
                    "SELECT c.inflight,c.uncertain FROM event_jobs j JOIN event_job_control c ON c.event_id=j.event_id "
                    "WHERE j.event_id=? AND j.status=? AND j.worker_id=? AND j.lease_until>? AND c.lease_token=?",
                    (job_id, EventJobStatus.RUNNING.value, worker_id, now, lease_token),
                ).fetchone()
                if row is None:
                    raise EventJobLeaseError("event lease no longer owned")
                uncertain = bool(row[0] or row[1])
                self._db.execute(
                    "UPDATE event_jobs SET status=?,worker_id=NULL,lease_until=NULL,updated_at=?,error_type=? "
                    "WHERE event_id=?",
                    (
                        EventJobStatus.UNKNOWN.value if uncertain else EventJobStatus.COMPLETED.value,
                        now,
                        "UncertainEffect" if uncertain else "Superseded" if superseded else None,
                        job_id,
                    ),
                )

            run_transaction(self._db, finish)

    async def fail_fenced(
        self, job_id: str, worker_id: str, lease_token: str, *, now: float, available_at: float, error_type: str
    ) -> None:
        async with self._lock:

            def fail() -> None:
                row = self._db.execute(
                    "SELECT j.side_effect_started,j.attempt,c.inflight,c.uncertain FROM event_jobs j "
                    "JOIN event_job_control c ON c.event_id=j.event_id WHERE j.event_id=? AND j.status=? "
                    "AND j.worker_id=? AND j.lease_until>? AND c.lease_token=?",
                    (job_id, EventJobStatus.RUNNING.value, worker_id, now, lease_token),
                ).fetchone()
                if row is None:
                    raise EventJobLeaseError("event lease no longer owned")
                state = (
                    EventJobStatus.UNKNOWN
                    if row[0] or row[2] or row[3]
                    else EventJobStatus.RETRY_WAIT if row[1] < self._max_attempts else EventJobStatus.FAILED
                )
                self._db.execute(
                    "UPDATE event_jobs SET status=?,worker_id=NULL,lease_until=NULL,updated_at=?,available_at=?, "
                    "error_type=? WHERE event_id=?",
                    (state.value, now, available_at, error_type, job_id),
                )

            run_transaction(self._db, fail)

    def _enqueue_in_transaction(
        self,
        event_id: str,
        payload: dict[str, Any],
        *,
        partition_key: str,
        now: float,
        private_message: bool = False,
    ) -> bool:
        """Insert into this store's already-open SQLite transaction."""
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        self._purge(now)
        cursor = self._db.execute(
            "INSERT INTO event_jobs "
            "(event_id, payload, partition_key, status, attempt, created_at, updated_at, available_at) "
            "VALUES (?, ?, ?, ?, 0, ?, ?, ?) ON CONFLICT(event_id) DO NOTHING",
            (event_id, encoded, partition_key, EventJobStatus.QUEUED.value, now, now, now),
        )
        if cursor.rowcount != 1:
            return False
        self._db.execute("INSERT INTO event_job_order(event_id) VALUES (?)", (event_id,))
        self._db.execute(
            "INSERT INTO event_job_control(event_id,private_message) VALUES (?,?)", (event_id, int(private_message))
        )
        return True

    async def claim_next(
        self,
        worker_id: str,
        *,
        now: float | None = None,
        lease_seconds: float = 60.0,
        availability_tolerance_seconds: float = 0.0,
    ) -> EventJob | None:
        current = time.time() if now is None else now
        if not worker_id:
            raise ValueError("worker_id must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if availability_tolerance_seconds < 0:
            raise ValueError("availability_tolerance_seconds must be non-negative")
        async with self._lock:
            claimed_event_id: str | None = None

            def claim() -> str | None:
                self._purge(current)
                self._recover_expired(current)
                row = self._db.execute(
                    "SELECT j.event_id FROM event_jobs AS j JOIN event_job_order AS seq ON seq.event_id=j.event_id "
                    "WHERE j.status IN (?, ?) AND j.available_at <= ? "
                    "AND NOT EXISTS (SELECT 1 FROM event_jobs AS prior "
                    "JOIN event_job_order AS prior_seq ON prior_seq.event_id=prior.event_id "
                    "WHERE prior.partition_key = j.partition_key AND prior.status != ? "
                    "AND prior_seq.sequence < seq.sequence) "
                    "ORDER BY seq.sequence LIMIT 1",
                    (
                        EventJobStatus.QUEUED.value,
                        EventJobStatus.RETRY_WAIT.value,
                        current + availability_tolerance_seconds,
                        EventJobStatus.COMPLETED.value,
                    ),
                ).fetchone()
                if row is None:
                    return None
                event_id = row[0]
                cursor = self._db.execute(
                    "UPDATE event_jobs SET status = ?, attempt = attempt + 1, worker_id = ?, lease_until = ?, "
                    "updated_at = ?, error_type = NULL WHERE event_id = ? AND status IN (?, ?)",
                    (
                        EventJobStatus.RUNNING.value,
                        worker_id,
                        current + lease_seconds,
                        current,
                        event_id,
                        EventJobStatus.QUEUED.value,
                        EventJobStatus.RETRY_WAIT.value,
                    ),
                )
                if cursor.rowcount != 1:
                    return None
                self._db.execute(
                    "INSERT INTO event_job_control(event_id,lease_token) VALUES (?,?) "
                    "ON CONFLICT(event_id) DO UPDATE SET lease_token=excluded.lease_token,inflight=0,uncertain=0",
                    (event_id, uuid.uuid4().hex),
                )
                return event_id

            claimed_event_id = run_transaction(self._db, claim)
            if claimed_event_id is None:
                return None
            claimed = self._get_locked(claimed_event_id)
            if claimed is None:
                raise EventJobStoreError(f"claimed job {claimed_event_id!r} disappeared from the store")
            return claimed

    async def renew(
        self, job_id: str, worker_id: str, *, now: float | None = None, lease_seconds: float = 60.0
    ) -> None:
        current = time.time() if now is None else now
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        async with self._lock:
            cursor = self._db.execute(
                "UPDATE event_jobs SET lease_until = ?, updated_at = ? "
                "WHERE event_id = ? AND status = ? AND worker_id = ?",
                (current + lease_seconds, current, job_id, EventJobStatus.RUNNING.value, worker_id),
            )
            self._db.commit()
            self._require_owner(cursor.rowcount, job_id, worker_id)

    async def mark_side_effect_started(self, job_id: str, worker_id: str) -> None:
        async with self._lock:
            cursor = self._db.execute(
                "UPDATE event_jobs SET side_effect_started = 1 " "WHERE event_id = ? AND status = ? AND worker_id = ?",
                (job_id, EventJobStatus.RUNNING.value, worker_id),
            )
            self._db.commit()
            self._require_owner(cursor.rowcount, job_id, worker_id)

    async def complete(self, job_id: str, worker_id: str, *, now: float | None = None) -> None:
        self._transition(job_id, worker_id, EventJobStatus.COMPLETED, now=now)

    async def retry(
        self, job_id: str, worker_id: str, *, available_at: float, error_type: str, now: float | None = None
    ) -> None:
        current = time.time() if now is None else now
        async with self._lock:
            job = self._get_locked(job_id)
            if job is None:
                raise EventJobLeaseError(f"worker {worker_id!r} does not own a running lease for job {job_id!r}")
            self._require_job_owner(job, job_id, worker_id)
            if job.side_effect_started:
                raise EventJobStoreError("cannot automatically retry after a side effect started")
            state = EventJobStatus.RETRY_WAIT if job.attempt < self._max_attempts else EventJobStatus.FAILED
            cursor = self._db.execute(
                "UPDATE event_jobs SET status = ?, available_at = ?, worker_id = NULL, lease_until = NULL, "
                "updated_at = ?, error_type = ? WHERE event_id = ? AND status = ? AND worker_id = ?",
                (state.value, available_at, current, error_type, job_id, EventJobStatus.RUNNING.value, worker_id),
            )
            self._db.commit()
            self._require_owner(cursor.rowcount, job_id, worker_id)

    async def mark_unknown(self, job_id: str, worker_id: str, *, error_type: str, now: float | None = None) -> None:
        current = time.time() if now is None else now
        async with self._lock:
            cursor = self._db.execute(
                "UPDATE event_jobs SET status = ?, worker_id = NULL, lease_until = NULL, "
                "updated_at = ?, error_type = ? "
                "WHERE event_id = ? AND status = ? AND worker_id = ?",
                (EventJobStatus.UNKNOWN.value, current, error_type, job_id, EventJobStatus.RUNNING.value, worker_id),
            )
            self._db.commit()
            self._require_owner(cursor.rowcount, job_id, worker_id)

    async def requeue_unknown(self, job_id: str, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        async with self._lock:
            cursor = self._db.execute(
                "UPDATE event_jobs SET status = ?, attempt = 0, available_at = ?, updated_at = ?, "
                "side_effect_started = 0, "
                "error_type = NULL WHERE event_id = ? AND status = ?",
                (EventJobStatus.QUEUED.value, current, current, job_id, EventJobStatus.UNKNOWN.value),
            )
            updated = cursor.rowcount == 1
            self._db.commit()
            if not updated:
                raise EventJobStoreError(f"job {job_id!r} is not unknown")

    async def requeue_failed(self, job_id: str, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        async with self._lock:
            cursor = self._db.execute(
                "UPDATE event_jobs SET status = ?, attempt = 0, available_at = ?, updated_at = ?, error_type = NULL "
                "WHERE event_id = ? AND status = ?",
                (EventJobStatus.QUEUED.value, current, current, job_id, EventJobStatus.FAILED.value),
            )
            updated = cursor.rowcount == 1
            self._db.commit()
            if not updated:
                raise EventJobStoreError(f"job {job_id!r} is not failed")

    async def get(self, job_id: str) -> EventJob | None:
        async with self._lock:
            return self._get_locked(job_id)

    async def next_wakeup(self) -> float | None:
        async with self._lock:
            row = self._db.execute(
                "SELECT MIN(CASE WHEN j.status IN (?, ?) THEN j.available_at "
                "WHEN j.status = ? THEN j.lease_until END) FROM event_jobs AS j "
                "JOIN event_job_order AS seq ON seq.event_id=j.event_id "
                "WHERE j.status IN (?, ?, ?) AND NOT EXISTS ("
                "SELECT 1 FROM event_jobs AS prior "
                "JOIN event_job_order AS prior_seq ON prior_seq.event_id=prior.event_id "
                "WHERE prior.partition_key = j.partition_key AND prior.status != ? "
                "AND prior_seq.sequence < seq.sequence)",
                (
                    EventJobStatus.QUEUED.value,
                    EventJobStatus.RETRY_WAIT.value,
                    EventJobStatus.RUNNING.value,
                    EventJobStatus.QUEUED.value,
                    EventJobStatus.RETRY_WAIT.value,
                    EventJobStatus.RUNNING.value,
                    EventJobStatus.COMPLETED.value,
                ),
            ).fetchone()
            return None if row is None or row[0] is None else float(row[0])

    async def close(self) -> None:
        async with self._lock:
            self._db.close()

    def _transition(self, job_id: str, worker_id: str, state: EventJobStatus, *, now: float | None) -> None:
        current = time.time() if now is None else now
        with self._db:
            cursor = self._db.execute(
                "UPDATE event_jobs SET status = ?, worker_id = NULL, lease_until = NULL, updated_at = ? "
                "WHERE event_id = ? AND status = ? AND worker_id = ?",
                (state.value, current, job_id, EventJobStatus.RUNNING.value, worker_id),
            )
            self._require_owner(cursor.rowcount, job_id, worker_id)

    def _recover_expired(self, now: float) -> None:
        rows = self._db.execute(
            "SELECT event_id, attempt, side_effect_started FROM event_jobs " "WHERE status = ? AND lease_until <= ?",
            (EventJobStatus.RUNNING.value, now),
        ).fetchall()
        for event_id, attempt, side_effect_started in rows:
            if side_effect_started:
                state = EventJobStatus.UNKNOWN
                error_type = "LeaseExpiredAfterSideEffect"
                available_at = now
            else:
                state = EventJobStatus.RETRY_WAIT if attempt < self._max_attempts else EventJobStatus.FAILED
                error_type = "LeaseExpired"
                available_at = now
            self._db.execute(
                "UPDATE event_jobs SET status = ?, worker_id = NULL, lease_until = NULL, updated_at = ?, "
                "available_at = ?, error_type = ? WHERE event_id = ?",
                (state.value, now, available_at, error_type, event_id),
            )

    def _purge(self, now: float) -> None:
        self._db.execute(
            "DELETE FROM event_job_control WHERE event_id IN (SELECT event_id FROM event_jobs "
            "WHERE (status=? AND updated_at+?<=?) OR (status=? AND ? IS NOT NULL AND updated_at+?<=?))",
            (
                EventJobStatus.COMPLETED.value,
                self._completed_ttl,
                now,
                EventJobStatus.UNKNOWN.value,
                self._unknown_ttl,
                self._unknown_ttl,
                now,
            ),
        )
        self._db.execute(
            "DELETE FROM event_job_order WHERE event_id IN "
            "(SELECT event_id FROM event_jobs WHERE status = ? AND updated_at + ? <= ?)",
            (EventJobStatus.COMPLETED.value, self._completed_ttl, now),
        )
        self._db.execute(
            "DELETE FROM event_jobs WHERE status = ? AND updated_at + ? <= ?",
            (EventJobStatus.COMPLETED.value, self._completed_ttl, now),
        )
        if self._unknown_ttl is not None:
            self._db.execute(
                "DELETE FROM event_job_order WHERE event_id IN "
                "(SELECT event_id FROM event_jobs WHERE status = ? AND updated_at + ? <= ?)",
                (EventJobStatus.UNKNOWN.value, self._unknown_ttl, now),
            )
            self._db.execute(
                "DELETE FROM event_jobs WHERE status = ? AND updated_at + ? <= ?",
                (EventJobStatus.UNKNOWN.value, self._unknown_ttl, now),
            )

    def _sync_receipt_order(self) -> None:
        # Backfill a quiesced legacy database in original rowid order. The
        # explicit INTEGER PRIMARY KEY survives VACUUM.
        self._db.execute(
            "INSERT INTO event_job_order(event_id) "
            "SELECT j.event_id FROM event_jobs AS j LEFT JOIN event_job_order AS seq ON seq.event_id=j.event_id "
            "WHERE seq.event_id IS NULL ORDER BY j.rowid"
        )

    def _get_locked(self, job_id: str) -> EventJob | None:
        row = self._db.execute(
            "SELECT j.event_id,j.payload,j.partition_key,j.status,j.attempt,j.created_at,j.updated_at,"
            "j.available_at,j.worker_id,j.lease_until,j.side_effect_started,j.error_type, "
            "c.lease_token,COALESCE(c.private_message,0) FROM event_jobs j "
            "LEFT JOIN event_job_control c ON c.event_id=j.event_id WHERE j.event_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return EventJob(
            job_id=row[0],
            event_id=row[0],
            payload=json.loads(row[1]),
            partition_key=row[2],
            status=EventJobStatus(row[3]),
            attempt=row[4],
            created_at=row[5],
            updated_at=row[6],
            available_at=row[7],
            worker_id=row[8],
            lease_until=row[9],
            side_effect_started=bool(row[10]),
            error_type=row[11],
            lease_token=row[12],
            private_message=bool(row[13]),
        )

    @staticmethod
    def _require_owner(changed: int, job_id: str, worker_id: str) -> None:
        if changed != 1:
            raise EventJobLeaseError(f"worker {worker_id!r} does not own a running lease for job {job_id!r}")

    def _require_job_owner(self, job: EventJob | None, job_id: str, worker_id: str) -> None:
        if job is None or job.status is not EventJobStatus.RUNNING or job.worker_id != worker_id:
            raise EventJobLeaseError(f"worker {worker_id!r} does not own a running lease for job {job_id!r}")


def _copy_job(job: EventJob) -> EventJob:
    return EventJob(
        job_id=job.job_id,
        event_id=job.event_id,
        payload=copy.deepcopy(job.payload),
        partition_key=job.partition_key,
        status=job.status,
        attempt=job.attempt,
        created_at=job.created_at,
        updated_at=job.updated_at,
        available_at=job.available_at,
        worker_id=job.worker_id,
        lease_until=job.lease_until,
        side_effect_started=job.side_effect_started,
        error_type=job.error_type,
        lease_token=job.lease_token,
        private_message=job.private_message,
    )
