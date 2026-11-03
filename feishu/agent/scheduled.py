"""Persistent primitives for explicitly confirmed agent schedules.

The store does not run agents or approve tools. A host must supply a runner and
must enforce its own authorization and side-effect policy before dispatch.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import math
import sqlite3
import time as time_module
import uuid
from collections import OrderedDict
from collections.abc import Awaitable
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, cast
from zoneinfo import ZoneInfo

from .._sqlite import connect as sqlite_connect
from ..auth import user_from_identity_keys, user_identity_keys

UTC = timezone.utc
_ACTIVE_RUNS = ("running", "awaiting_authorization", "awaiting_confirmation", "unknown")
_FINAL_RUNS = {"succeeded", "failed", "delivery_failed", "awaiting_authorization", "awaiting_confirmation", "unknown"}
logger = logging.getLogger(__name__)


def _number(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be finite")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _instant(value: float, name: str) -> float:
    result = _number(value, name)
    if not -62135596800 <= result <= 253402300799:
        raise ValueError(f"{name} is outside the supported datetime range")
    return result


def _keys(keys: tuple[str, ...]) -> tuple[str, ...]:
    result = tuple(sorted({key for key in keys if isinstance(key, str) and key.strip()}))
    if not result or tuple(sorted(user_identity_keys(user_from_identity_keys(result)))) != result:
        raise ValueError("valid, unambiguous authenticated owner keys are required")
    return result


def _tenant(tenant_key: str) -> str:
    if not isinstance(tenant_key, str) or not tenant_key.strip() or tenant_key != tenant_key.strip():
        raise ValueError("an authenticated tenant is required")
    return tenant_key


def _is_owner(stored: tuple[str, ...], actual: tuple[str, ...]) -> bool:
    return bool(set(stored).intersection(_keys(actual)))


@dataclass(frozen=True)
class Schedule:
    """Typed schedule. Time values are UTC seconds; weekdays are Monday=0."""

    kind: Literal["once", "interval", "calendar"]
    run_at: float | None = None
    seconds: float | None = None
    anchor_at: float | None = None
    hour: int | None = None
    minute: int | None = None
    weekdays: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.kind == "once":
            _instant(self.run_at, "run_at")  # type: ignore[arg-type]
            if any(
                (
                    self.seconds is not None,
                    self.anchor_at is not None,
                    self.hour is not None,
                    self.minute is not None,
                    self.weekdays,
                )
            ):
                raise ValueError("once schedule has unexpected fields")
        elif self.kind == "interval":
            if _number(self.seconds, "seconds") < 60:  # type: ignore[arg-type]
                raise ValueError("interval must be at least 60 seconds")
            _instant(self.anchor_at, "anchor_at")  # type: ignore[arg-type]
            if any((self.run_at is not None, self.hour is not None, self.minute is not None, self.weekdays)):
                raise ValueError("interval schedule has unexpected fields")
        elif self.kind == "calendar":
            if any(isinstance(v, bool) or not isinstance(v, int) for v in (self.hour, self.minute)):
                raise ValueError("calendar hour and minute must be integers")
            if not 0 <= self.hour <= 23 or not 0 <= self.minute <= 59:  # type: ignore[operator]
                raise ValueError("invalid local time")
            if any(isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6 for day in self.weekdays):
                raise ValueError("weekdays must be integers from 0 to 6")
            if any((self.run_at is not None, self.seconds is not None, self.anchor_at is not None)):
                raise ValueError("calendar schedule has unexpected fields")
        else:
            raise ValueError("unknown schedule kind")

    @classmethod
    def once(cls, run_at: float) -> Schedule:
        return cls(kind="once", run_at=run_at)

    @classmethod
    def interval(cls, *, seconds: float, anchor_at: float) -> Schedule:
        return cls(kind="interval", seconds=seconds, anchor_at=anchor_at)

    @classmethod
    def calendar(cls, *, hour: int, minute: int, weekdays: tuple[int, ...] = ()) -> Schedule:
        return cls(kind="calendar", hour=hour, minute=minute, weekdays=tuple(sorted(set(weekdays))))

    @classmethod
    def from_dict(cls, raw: dict) -> Schedule:
        return cls(**{**raw, "weekdays": tuple(raw.get("weekdays", ()))})

    def next_after(self, after: float, zone: str) -> float | None:
        """Find the next strictly later UTC occurrence in the supplied timezone."""
        after = _instant(after, "after")
        tz = ZoneInfo(zone)
        if self.kind == "once":
            return self.run_at if self.run_at > after else None  # type: ignore[operator]
        if self.kind == "interval":
            anchor, seconds = float(self.anchor_at), float(self.seconds)  # type: ignore[arg-type]
            n = max(0, math.floor((after - anchor) / seconds) + 1)
            next_at = anchor + n * seconds
            return next_at if next_at <= 253402300799 else None
        local_date = datetime.fromtimestamp(after, tz).date()
        for day_offset in range(9):
            current = local_date + timedelta(days=day_offset)
            if self.weekdays and current.weekday() not in self.weekdays:
                continue
            candidate = _calendar_instant(current, int(self.hour), int(self.minute), tz)  # type: ignore[arg-type]
            if candidate > after:
                return candidate
        raise ValueError("could not find next calendar occurrence")


def _calendar_instant(day: date, hour: int, minute: int, zone: ZoneInfo) -> float:
    # Validate by round-tripping UTC. fold=0 chooses the first occurrence of an
    # overlap; stepping by minutes picks the first valid local instant after a gap.
    naive = datetime.combine(day, time(hour, minute))
    for offset in range(24 * 60 + 1):
        candidate = naive + timedelta(minutes=offset)
        local = candidate.replace(tzinfo=zone, fold=0)
        instant = local.astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == candidate:
            return instant.timestamp()
    raise ValueError("no valid local time near scheduled date")


@dataclass(frozen=True)
class ScheduledDraft:
    draft_id: str
    payload_hash: str
    expires_at: float


@dataclass(frozen=True)
class ScheduledTask:
    task_id: str
    tenant_key: str
    owner_keys: tuple[str, ...]
    chat_id: str
    instruction: str
    schedule: Schedule
    policy: str
    timezone: str
    state: str
    next_run_at: float | None
    revision: int


@dataclass(frozen=True)
class ScheduledRun:
    run_id: str
    task_id: str
    scheduled_for: float
    status: str
    session_id: str
    started_at: float | None
    finished_at: float | None
    summary: str | None
    delivery_status: str
    delivery_deadline: float | None
    wait_kind: str | None
    wait_id: str | None
    wait_deadline: float | None
    lease_token: str | None
    lease_until: float | None


@dataclass(frozen=True)
class ScheduledExecutionResult:
    """Outcome supplied by a trusted executor after applying its own policy."""

    status: Literal[
        "succeeded",
        "failed",
        "delivery_failed",
        "awaiting_authorization",
        "awaiting_confirmation",
        "unknown",
        "missed",
        "skipped_overlap",
    ]
    summary: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _FINAL_RUNS | {"missed", "skipped_overlap"}:
            raise ValueError("invalid execution status")
        if self.summary is not None and (not isinstance(self.summary, str) or len(self.summary) > 2000):
            raise ValueError("summary must be at most 2000 characters")


class ScheduledRunConflict(ValueError):
    """The run was advanced by a different worker or callback."""


class ScheduledTaskStore:
    """SQLite-backed confirmation, ownership, schedule and run history store.

    Each method creates its own connection; SQLite's IMMEDIATE transactions make
    confirmation and claim atomic across processes sharing the same database.
    Claimed runs are not automatically replayed if their worker disappears.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("use a persistent SQLite path")
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS scheduled_drafts (
                    draft_id TEXT PRIMARY KEY, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    task_id TEXT PRIMARY KEY, owner_keys TEXT NOT NULL, chat_id TEXT NOT NULL,
                    instruction TEXT NOT NULL, schedule TEXT NOT NULL, policy TEXT NOT NULL,
                    timezone TEXT NOT NULL, state TEXT NOT NULL, next_run_at REAL,
                    revision INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    tenant_key TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS scheduled_tasks_due ON scheduled_tasks(state, next_run_at);
                CREATE TABLE IF NOT EXISTS scheduled_runs (
                    run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, scheduled_for REAL NOT NULL,
                    status TEXT NOT NULL, session_id TEXT NOT NULL, started_at REAL,
                    finished_at REAL, summary TEXT, delivery_status TEXT NOT NULL DEFAULT 'pending',
                    UNIQUE(task_id, scheduled_for)
                );
                CREATE INDEX IF NOT EXISTS scheduled_runs_task ON scheduled_runs(task_id, scheduled_for);
                CREATE TABLE IF NOT EXISTS scheduled_reconciliations (
                    run_id TEXT PRIMARY KEY, tenant_key TEXT NOT NULL, actor TEXT NOT NULL,
                    decision TEXT NOT NULL, reason TEXT NOT NULL, reconciled_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scheduled_delivery_attempts (
                    token TEXT PRIMARY KEY, run_id TEXT NOT NULL, status TEXT NOT NULL,
                    started_at REAL NOT NULL, finished_at REAL
                );
            """)
            db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(scheduled_runs)")}
            if "delivery_status" not in columns:
                db.execute("ALTER TABLE scheduled_runs ADD COLUMN delivery_status TEXT NOT NULL DEFAULT 'pending'")
            for name, field_type in (
                ("wait_kind", "TEXT"),
                ("wait_id", "TEXT"),
                ("wait_deadline", "REAL"),
                ("delivery_deadline", "REAL"),
                ("delivery_token", "TEXT"),
                ("lease_token", "TEXT"),
                ("lease_until", "REAL"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE scheduled_runs ADD COLUMN {name} {field_type}")
            task_columns = {row["name"] for row in db.execute("PRAGMA table_info(scheduled_tasks)")}
            if "tenant_key" not in task_columns:
                db.execute("ALTER TABLE scheduled_tasks ADD COLUMN tenant_key TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite_connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_draft(
        self,
        *,
        tenant_key: str,
        owner_keys: tuple[str, ...],
        chat_id: str,
        instruction: str,
        schedule: Schedule,
        now: float,
        policy: str = "read_only",
        timezone: str = "UTC",
        ttl: float = 1800,
    ) -> ScheduledDraft:
        tenant_key, owner = _tenant(tenant_key), _keys(owner_keys)
        now, ttl = _number(now, "now"), _number(ttl, "ttl")
        if (
            ttl <= 0
            or not isinstance(chat_id, str)
            or not chat_id.strip()
            or not isinstance(instruction, str)
            or not instruction.strip()
        ):
            raise ValueError("positive ttl, chat_id and instruction required")
        if policy not in ("read_only", "confirm_writes"):
            raise ValueError("unsupported scheduled policy")
        if schedule.next_after(now, timezone) is None:
            raise ValueError("schedule has no future occurrence")
        payload = json.dumps(
            {
                "owner_keys": owner,
                "tenant_key": tenant_key,
                "chat_id": chat_id,
                "instruction": instruction,
                "schedule": asdict(schedule),
                "policy": policy,
                "timezone": timezone,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()
        result = ScheduledDraft(uuid.uuid4().hex, digest, now + ttl)
        with self._connect() as db:
            db.execute(
                "INSERT INTO scheduled_drafts VALUES (?, ?, ?, ?)",
                (result.draft_id, payload, digest, result.expires_at),
            )
        return result

    def confirm_draft(
        self, draft_id: str, owner_keys: tuple[str, ...], payload_hash: str, *, tenant_key: str, now: float
    ) -> ScheduledTask:
        now = _number(now, "now")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM scheduled_drafts WHERE draft_id=?", (draft_id,)).fetchone()
            if row is None or row["expires_at"] <= now:
                raise ValueError("draft missing or expired")
            if (
                row["payload_hash"] != payload_hash
                or hashlib.sha256(row["payload"].encode()).hexdigest() != payload_hash
            ):
                raise ValueError("draft payload hash mismatch")
            data = json.loads(row["payload"])
            if data.get("tenant_key") != _tenant(tenant_key) or not _is_owner(tuple(data["owner_keys"]), owner_keys):
                raise PermissionError("only the creator may confirm")
            schedule = Schedule.from_dict(data["schedule"])
            next_run = schedule.next_after(now, data["timezone"])
            if next_run is None:
                raise ValueError("schedule has no future occurrence")
            task_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO scheduled_tasks "
                "(task_id, owner_keys, chat_id, instruction, schedule, policy, timezone, state, "
                "next_run_at, revision, created_at, updated_at, tenant_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, 1, ?, ?, ?)",
                (
                    task_id,
                    json.dumps(data["owner_keys"]),
                    data["chat_id"],
                    data["instruction"],
                    json.dumps(data["schedule"]),
                    data["policy"],
                    data["timezone"],
                    next_run,
                    now,
                    now,
                    tenant_key,
                ),
            )
            db.execute("DELETE FROM scheduled_drafts WHERE draft_id=?", (draft_id,))
            row = db.execute("SELECT * FROM scheduled_tasks WHERE task_id=?", (task_id,)).fetchone()
            return _task(row)

    def get(self, task_id: str) -> ScheduledTask | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM scheduled_tasks WHERE task_id=?", (task_id,)).fetchone()
            return _task(row) if row else None

    def list_owned(self, owner_keys: tuple[str, ...], *, tenant_key: str) -> list[ScheduledTask]:
        owner = _keys(owner_keys)
        tenant_key = _tenant(tenant_key)
        with self._connect() as db:
            return [
                task
                for row in db.execute(
                    "SELECT * FROM scheduled_tasks WHERE state != 'deleted' AND tenant_key=? ORDER BY created_at",
                    (tenant_key,),
                )
                if _is_owner((task := _task(row)).owner_keys, owner)
            ]

    def set_state(
        self,
        task_id: str,
        owner_keys: tuple[str, ...],
        state: str,
        *,
        tenant_key: str,
        expected_revision: int,
        now: float,
    ) -> ScheduledTask:
        if state not in ("active", "paused", "deleted"):
            raise ValueError("invalid task state")
        now = _number(now, "now")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM scheduled_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise ValueError("task not found")
            task = _task(row)
            if task.tenant_key != _tenant(tenant_key) or not _is_owner(task.owner_keys, owner_keys):
                raise PermissionError("only the creator may manage this task")
            if (
                task.revision != expected_revision
                or task.state == "deleted"
                or (task.state == "completed" and state != "deleted")
            ):
                raise ValueError("stale revision or terminal task")
            next_run = task.next_run_at
            if state == "active" and task.state != "active":
                next_run = task.schedule.next_after(now, task.timezone)
                if next_run is None:
                    if task.schedule.kind != "once" or task.next_run_at is None:
                        raise ValueError("schedule has no future occurrence")
                    run_id = uuid.uuid4().hex
                    db.execute(
                        "INSERT INTO scheduled_runs "
                        "(run_id, task_id, scheduled_for, status, session_id, finished_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (run_id, task.task_id, task.next_run_at, "missed", f"scheduled:{task.task_id}:{run_id}", now),
                    )
                    state = "completed"
            db.execute(
                "UPDATE scheduled_tasks SET state=?, next_run_at=?, revision=revision+1, updated_at=? WHERE task_id=?",
                (state, next_run, now, task_id),
            )
            return _task(db.execute("SELECT * FROM scheduled_tasks WHERE task_id=?", (task_id,)).fetchone())

    def refresh_timezones(
        self, timezone_for: Callable[[ScheduledTask], str], *, now: float, task_ids: tuple[str, ...] | None = None
    ) -> None:
        """Recompute future calendar triggers when their trusted timezone changes.

        A trigger already due is left for claim_due to settle as run or missed.
        The callback runs without holding a database transaction.
        """
        now = _number(now, "now")
        with self._connect() as db:
            if task_ids is None:
                rows = db.execute("SELECT * FROM scheduled_tasks WHERE state='active'").fetchall()
            elif not task_ids:
                return
            elif len(task_ids) <= 1024:
                marks = ",".join("?" for _ in task_ids)
                rows = db.execute(
                    f"SELECT * FROM scheduled_tasks WHERE state='active' AND task_id IN ({marks})", task_ids
                ).fetchall()
            else:
                raise ValueError("too many timezone refresh tasks")
        tasks = []
        for row in rows:
            try:
                tasks.append(_task(row))
            except (ValueError, TypeError, OverflowError, OSError, KeyError):
                logger.warning("quarantining invalid scheduled task %s", row["task_id"])
                with self._connect() as db:
                    db.execute(
                        "UPDATE scheduled_tasks SET state='error', next_run_at=NULL, revision=revision+1, updated_at=? "
                        "WHERE task_id=? AND state='active' AND revision=?",
                        (now, row["task_id"], row["revision"]),
                    )
        for task in tasks:
            if task.schedule.kind != "calendar":
                continue
            try:
                zone = timezone_for(task)
                if zone == task.timezone or task.next_run_at is None or task.next_run_at <= now:
                    continue
                next_run = task.schedule.next_after(now, zone)
            except Exception:  # noqa: BLE001 - one broken resolver must not block every task
                logger.warning("quarantining scheduled task with invalid timezone %s", task.task_id)
                with self._connect() as db:
                    db.execute(
                        "UPDATE scheduled_tasks SET state='error', next_run_at=NULL, revision=revision+1, updated_at=? "
                        "WHERE task_id=? AND state='active' AND revision=?",
                        (now, task.task_id, task.revision),
                    )
                continue
            with self._connect() as db:
                db.execute(
                    "UPDATE scheduled_tasks SET timezone=?, next_run_at=?, revision=revision+1, updated_at=? "
                    "WHERE task_id=? AND state='active' AND revision=?",
                    (zone, next_run, now, task.task_id, task.revision),
                )

    def active_calendar_batch(self, *, cursor: str = "", limit: int = 128) -> tuple[list[ScheduledTask], str]:
        if (
            not isinstance(cursor, str)
            or not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 1024
        ):
            raise ValueError("invalid calendar scan bounds")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM scheduled_tasks WHERE state='active' AND task_id>? ORDER BY task_id LIMIT ?",
                (cursor, limit),
            ).fetchall()
        tasks = []
        for row in rows:
            try:
                task = _task(row)
            except (ValueError, TypeError, OverflowError, OSError, KeyError):
                continue
            if task.schedule.kind == "calendar":
                tasks.append(task)
        next_cursor = rows[-1]["task_id"] if len(rows) == limit else ""
        return tasks, next_cursor

    def claim_due(
        self,
        *,
        now: float,
        timezone_for: Callable[[ScheduledTask], str] | None = None,
        grace_seconds: float = 60,
        lease_seconds: float = 60,
    ) -> ScheduledRun | None:
        now, grace_seconds = _number(now, "now"), _number(grace_seconds, "grace_seconds")
        lease_seconds = _number(lease_seconds, "lease_seconds")
        if grace_seconds < 0 or lease_seconds <= 0:
            raise ValueError("grace_seconds must be nonnegative and lease_seconds positive")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            while True:
                row = db.execute(
                    "SELECT * FROM scheduled_tasks WHERE state='active' AND next_run_at<=? "
                    "ORDER BY next_run_at, task_id LIMIT 1",
                    (now,),
                ).fetchone()
                if row is None:
                    return None
                try:
                    task = _task(row)
                    _tenant(task.tenant_key)
                    _keys(task.owner_keys)
                    zone = timezone_for(task) if timezone_for else task.timezone
                    next_run = task.schedule.next_after(now, zone)
                    break
                except Exception:  # noqa: BLE001 - isolate invalid stored schedules or timezone resolvers
                    logger.warning("quarantining invalid scheduled task %s", row["task_id"])
                    db.execute(
                        "UPDATE scheduled_tasks SET state='error', next_run_at=NULL, revision=revision+1, updated_at=? "
                        "WHERE task_id=?",
                        (now, row["task_id"]),
                    )
            state = "completed" if next_run is None else "active"
            occurrence = float(task.next_run_at)  # type: ignore[arg-type]
            overlap = db.execute(
                "SELECT 1 FROM scheduled_runs WHERE task_id=? AND status IN (?, ?, ?, ?) LIMIT 1",
                (task.task_id, *_ACTIVE_RUNS),
            ).fetchone()
            status = "missed" if now - occurrence > grace_seconds else "skipped_overlap" if overlap else "running"
            run_id = uuid.uuid4().hex
            session = f"scheduled:{task.task_id}:{run_id}"
            db.execute(
                "INSERT INTO scheduled_runs "
                "(run_id, task_id, scheduled_for, status, session_id, started_at, "
                "finished_at, lease_token, lease_until) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    task.task_id,
                    occurrence,
                    status,
                    session,
                    now if status == "running" else None,
                    None if status == "running" else now,
                    uuid.uuid4().hex if status == "running" else None,
                    now + lease_seconds if status == "running" else None,
                ),
            )
            db.execute(
                "UPDATE scheduled_tasks SET state=?, next_run_at=?, revision=revision+1, updated_at=? WHERE task_id=?",
                (state, next_run, now, task.task_id),
            )
            return _run(db.execute("SELECT * FROM scheduled_runs WHERE run_id=?", (run_id,)).fetchone())

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        now: float,
        summary: str | None = None,
        expected_status: str | None = None,
        expected_token: str | None = None,
        reset_delivery: bool = False,
    ) -> ScheduledRun:
        if status not in _FINAL_RUNS:
            raise ValueError("invalid run status")
        now = _number(now, "now")
        if summary is not None and (not isinstance(summary, str) or len(summary) > 2000):
            raise ValueError("summary must be at most 2000 characters")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM scheduled_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None or row["status"] not in _ACTIVE_RUNS or row["status"] == "unknown":
                raise ScheduledRunConflict("run not active")
            if expected_status is not None and row["status"] != expected_status:
                raise ScheduledRunConflict("run status changed")
            if row["status"] == "running" and not expected_token:
                raise ScheduledRunConflict("running task requires its lease token")
            if expected_token is not None and (
                row["lease_token"] != expected_token or row["lease_until"] is None or row["lease_until"] <= now
            ):
                raise ScheduledRunConflict("run lease expired or changed")
            finished = None if status in _ACTIVE_RUNS else now
            if reset_delivery and row["delivery_token"]:
                db.execute(
                    "UPDATE scheduled_delivery_attempts SET status='unknown', finished_at=? "
                    "WHERE token=? AND status='sending'",
                    (now, row["delivery_token"]),
                )
            db.execute(
                "UPDATE scheduled_runs SET status=?, finished_at=?, summary=?, "
                "delivery_status=CASE WHEN ? THEN 'pending' ELSE delivery_status END, "
                "delivery_token=CASE WHEN ? THEN NULL ELSE delivery_token END, "
                "delivery_deadline=CASE WHEN ? THEN NULL ELSE delivery_deadline END, lease_until=NULL WHERE run_id=?",
                (status, finished, summary, reset_delivery, reset_delivery, reset_delivery, run_id),
            )
            return _run(db.execute("SELECT * FROM scheduled_runs WHERE run_id=?", (run_id,)).fetchone())

    def get_run(self, run_id: str) -> ScheduledRun | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM scheduled_runs WHERE run_id=?", (run_id,)).fetchone()
            return _run(row) if row else None

    def validate_lease(self, run_id: str, token: str | None, *, now: float) -> None:
        if not token:
            raise PermissionError("scheduled lease is missing")
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM scheduled_runs WHERE run_id=? AND status='running' AND lease_token=? AND lease_until>?",
                (run_id, token, _number(now, "now")),
            ).fetchone()
        if row is None:
            raise PermissionError("scheduled lease expired or changed")

    def renew_lease(self, run_id: str, token: str | None, *, now: float, lease_seconds: float = 60) -> bool:
        now, lease_seconds = _number(now, "now"), _number(lease_seconds, "lease_seconds")
        if not token or lease_seconds <= 0:
            raise ValueError("valid lease token and duration required")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE scheduled_runs SET lease_until=? WHERE run_id=? AND status='running' "
                "AND lease_token=? AND lease_until>?",
                (now + lease_seconds, run_id, token, now),
            )
            return cursor.rowcount == 1

    def expire_runs(self, *, now: float) -> int:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = _number(now, "now")
            db.execute(
                "UPDATE scheduled_delivery_attempts SET status='unknown', finished_at=? "
                "WHERE status='sending' AND token IN (SELECT delivery_token FROM scheduled_runs "
                "WHERE status='running' AND (lease_until IS NULL OR lease_until<=?))",
                (now, now),
            )
            cursor = db.execute(
                "UPDATE scheduled_runs SET status='unknown', summary='execution lease expired', lease_until=NULL, "
                "delivery_status='pending', delivery_token=NULL, delivery_deadline=NULL "
                "WHERE status='running' AND (lease_until IS NULL OR lease_until<=?)",
                (now,),
            )
            return cursor.rowcount

    def unknown_runs(self, *, tenant_key: str) -> list[ScheduledRun]:
        """List a tenant's frozen runs; the caller must authorize the operator."""
        tenant_key = _tenant(tenant_key)
        with self._connect() as db:
            return [
                _run(row)
                for row in db.execute(
                    "SELECT r.* FROM scheduled_runs r JOIN scheduled_tasks t ON r.task_id=t.task_id "
                    "WHERE r.status='unknown' AND t.tenant_key=? ORDER BY r.scheduled_for",
                    (tenant_key,),
                )
            ]

    def reconcile_unknown(
        self, run_id: str, *, tenant_key: str, actor: str, decision: str, reason: str, now: float
    ) -> ScheduledRun:
        """Record an authorized operator's external reconciliation; never replay work.

        Authorization of the actor and the correctness of the investigation are
        the host's responsibility. Only a frozen run in this tenant is accepted.
        """
        tenant_key, now = _tenant(tenant_key), _number(now, "now")
        if (
            not isinstance(actor, str)
            or not actor.strip()
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 2000
            or decision not in ("confirmed_succeeded", "confirmed_failed", "abandoned")
        ):
            raise ValueError("actor, reason and explicit reconciliation decision are required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT r.*, t.tenant_key FROM scheduled_runs r JOIN scheduled_tasks t ON r.task_id=t.task_id "
                "WHERE r.run_id=?",
                (run_id,),
            ).fetchone()
            if row is None or row["tenant_key"] != tenant_key:
                raise PermissionError("unknown run is unavailable in this tenant")
            if row["status"] != "unknown":
                raise ScheduledRunConflict("run is not frozen")
            db.execute(
                "INSERT INTO scheduled_reconciliations VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, tenant_key, actor, decision, reason, now),
            )
            status = "succeeded" if decision == "confirmed_succeeded" else "failed"
            db.execute(
                "UPDATE scheduled_runs SET status=?, finished_at=?, lease_token=NULL, lease_until=NULL, "
                "wait_kind=NULL, wait_id=NULL, wait_deadline=NULL WHERE run_id=? AND status='unknown'",
                (status, now, run_id),
            )
            return _run(db.execute("SELECT * FROM scheduled_runs WHERE run_id=?", (run_id,)).fetchone())

    def claim_resume(
        self, run_id: str, wait_id: str, *, expected_status: str, now: float, lease_seconds: float = 60
    ) -> ScheduledRun:
        now, lease_seconds = _number(now, "now"), _number(lease_seconds, "lease_seconds")
        if (
            expected_status not in ("awaiting_confirmation", "awaiting_authorization")
            or not wait_id
            or lease_seconds <= 0
        ):
            raise ValueError("invalid continuation lease")
        token = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE scheduled_runs SET status='running', lease_token=?, lease_until=? "
                "WHERE run_id=? AND status=? AND wait_id=? AND wait_kind=? AND EXISTS "
                "(SELECT 1 FROM scheduled_tasks WHERE task_id=scheduled_runs.task_id "
                "AND state IN ('active', 'completed') AND tenant_key != '')",
                (
                    token,
                    now + lease_seconds,
                    run_id,
                    expected_status,
                    wait_id,
                    "approval" if expected_status == "awaiting_confirmation" else "authorization",
                ),
            )
            if cursor.rowcount != 1:
                raise ScheduledRunConflict("scheduled continuation was already claimed or changed")
            return _run(db.execute("SELECT * FROM scheduled_runs WHERE run_id=?", (run_id,)).fetchone())

    def record_wait(
        self, run_id: str, kind: str, wait_id: str, deadline: float, *, expected_token: str, now: float
    ) -> ScheduledRun:
        """Record a persisted continuation before its card becomes actionable."""
        if kind not in ("approval", "authorization") or not wait_id:
            raise ValueError("invalid continuation")
        deadline, now = _number(deadline, "deadline"), _number(now, "now")
        if not expected_token:
            raise ValueError("lease token is required")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE scheduled_runs SET wait_kind=?, wait_id=?, wait_deadline=? "
                "WHERE run_id=? AND status='running' AND lease_token=? AND lease_until>?",
                (kind, wait_id, deadline, run_id, expected_token, now),
            )
            if cursor.rowcount != 1:
                raise ScheduledRunConflict("run cannot accept a continuation")
            return _run(db.execute("SELECT * FROM scheduled_runs WHERE run_id=?", (run_id,)).fetchone())

    def clear_wait(self, run_id: str, wait_id: str, *, expected_token: str, now: float) -> None:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE scheduled_runs SET wait_kind=NULL, wait_id=NULL, wait_deadline=NULL "
                "WHERE run_id=? AND wait_id=? AND status='running' AND lease_token=? AND lease_until>?",
                (run_id, wait_id, expected_token, _number(now, "now")),
            )
            if cursor.rowcount != 1:
                raise ScheduledRunConflict("run lease expired or continuation changed")

    def expired_waits(self, *, now: float) -> list[ScheduledRun]:
        with self._connect() as db:
            return [
                _run(row)
                for row in db.execute(
                    "SELECT * FROM scheduled_runs WHERE "
                    "((status='awaiting_confirmation' AND wait_kind='approval') OR "
                    "(status='awaiting_authorization' AND wait_kind='authorization')) "
                    "AND wait_deadline<=? ORDER BY wait_deadline",
                    (_number(now, "now"),),
                )
            ]

    def runs(self, task_id: str) -> list[ScheduledRun]:
        with self._connect() as db:
            return [
                _run(row)
                for row in db.execute("SELECT * FROM scheduled_runs WHERE task_id=? ORDER BY scheduled_for", (task_id,))
            ]

    def claim_delivery(self, run_id: str, *, now: float, lease_seconds: float = 60) -> str | None:
        now, lease_seconds = _number(now, "now"), _number(lease_seconds, "lease_seconds")
        if lease_seconds <= 0:
            raise ValueError("delivery lease must be positive")
        token = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE scheduled_runs SET delivery_status='sending', delivery_deadline=?, delivery_token=? "
                "WHERE run_id=? AND status!='running' AND delivery_status='pending'",
                (now + lease_seconds, token, run_id),
            )
            if cursor.rowcount != 1:
                return None
            db.execute(
                "INSERT INTO scheduled_delivery_attempts VALUES (?, ?, 'sending', ?, NULL)", (token, run_id, now)
            )
            return token

    def expire_deliveries(self, *, now: float) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = _number(now, "now")
            db.execute(
                "UPDATE scheduled_delivery_attempts SET status='unknown', finished_at=? "
                "WHERE status='sending' AND token IN (SELECT delivery_token FROM scheduled_runs "
                "WHERE delivery_status='sending' AND delivery_deadline<=?)",
                (now, now),
            )
            db.execute(
                "UPDATE scheduled_runs SET delivery_status='unknown' "
                "WHERE delivery_status='sending' AND delivery_deadline<=?",
                (now,),
            )

    def record_delivery(self, run_id: str, status: str, *, token: str, now: float) -> ScheduledRun:
        """Record delivery independently from execution, without retrying an uncertain send."""
        if status not in ("delivered", "unknown"):
            raise ValueError("invalid delivery status")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE scheduled_runs SET delivery_status=?, delivery_deadline=NULL, delivery_token=NULL "
                "WHERE run_id=? AND delivery_status='sending' AND delivery_token=?",
                (status, run_id, token),
            )
            if cursor.rowcount != 1:
                raise ValueError("run not found or delivery already settled")
            db.execute(
                "UPDATE scheduled_delivery_attempts SET status=?, finished_at=? WHERE token=?",
                (status, _number(now, "now"), token),
            )
            return _run(db.execute("SELECT * FROM scheduled_runs WHERE run_id=?", (run_id,)).fetchone())

    def pending_deliveries(self, *, limit: int = 128) -> list[ScheduledRun]:
        """Return notices never yet submitted; uncertain attempts are never replayed."""
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1024:
            raise ValueError("invalid delivery scan bound")
        with self._connect() as db:
            return [
                _run(row)
                for row in db.execute(
                    "SELECT * FROM scheduled_runs WHERE status != 'running' "
                    "AND delivery_status='pending' ORDER BY scheduled_for LIMIT ?",
                    (limit,),
                )
            ]


class ScheduledTimezoneResolver:
    """Resolve a creator's current timezone from a tenant-verified OAuth profile."""

    def __init__(
        self,
        user_tokens: Any,
        client: Any,
        *,
        ttl_seconds: float = 3600,
        max_entries: int = 2048,
        max_requests_per_cycle: int = 10,
        clock: Callable[[], float] = time_module.monotonic,
    ) -> None:
        self.user_tokens, self.client, self.clock = user_tokens, client, clock
        self.ttl_seconds = _number(ttl_seconds, "ttl_seconds")
        if self.ttl_seconds <= 0 or max_entries < 1 or max_requests_per_cycle < 1:
            raise ValueError("timezone cache bounds must be positive")
        self.max_entries, self.max_requests_per_cycle = max_entries, max_requests_per_cycle
        self._budget = max_requests_per_cycle
        self._cache: OrderedDict[tuple[str, tuple[str, ...]], tuple[float, str | None]] = OrderedDict()

    def begin_cycle(self) -> None:
        self._budget = self.max_requests_per_cycle

    async def __call__(self, task: ScheduledTask) -> str:
        key = (task.tenant_key, task.owner_keys)
        now = self.clock()
        cached = self._cache.get(key)
        if cached and now < cached[0]:
            self._cache.move_to_end(key)
            return cached[1] or task.timezone
        if self._budget <= 0:
            return task.timezone
        self._budget -= 1
        # Suppress duplicate lookups and throttle retries even if the outer
        # scheduler cancels this request to meet its per-cycle deadline.
        self._cache[key] = (now + min(self.ttl_seconds, 60), None)
        zone: str | None = None
        with suppress(Exception):
            zone = await asyncio.wait_for(self._fetch(task), timeout=5)
        self._cache[key] = (now + self.ttl_seconds, zone)
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)
        return zone or task.timezone

    async def _fetch(self, task: ScheduledTask) -> str | None:
        from .prompting import _extract_timezone

        token_getter = getattr(self.user_tokens, "user_token_for_tenant", None)
        user_info = getattr(getattr(self.client, "oauth", None), "user_info", None)
        if not callable(token_getter) or not callable(user_info):
            return None
        token = token_getter(task.tenant_key, user_from_identity_keys(task.owner_keys))
        if inspect.isawaitable(token):
            token = await token
        if not token:
            return None
        info = user_info(token)
        if inspect.isawaitable(info):
            info = await info
        if not isinstance(info, dict):
            return None
        nodes = [info]
        for node in nodes:
            for key in ("data", "user", "profile"):
                child = node.get(key)
                if isinstance(child, dict) and child not in nodes:
                    nodes.append(child)
            if len(nodes) > 20:
                return None
        tenants = {
            node["tenant_key"] for node in nodes if isinstance(node.get("tenant_key"), str) and node["tenant_key"]
        }
        identities = {identity for node in nodes for identity in user_identity_keys(node)}
        if tenants != {task.tenant_key} or not identities.intersection(task.owner_keys):
            return None
        return _extract_timezone(info)


class ScheduledTaskRunner:
    """Run one due task at a time using application-owned async callbacks.

    The executor is a trusted adapter, responsible for owner-bound identity,
    scheduled side-effect policy, and approval/OAuth handling. The runner does
    not construct Feishu events or make those guarantees on its own. A process
    failure leaves the run observable rather than automatically replaying it.
    """

    def __init__(
        self,
        store: ScheduledTaskStore,
        executor: Callable[[ScheduledTask, ScheduledRun], Awaitable[ScheduledExecutionResult]],
        notifier: Callable[[ScheduledTask, ScheduledRun, ScheduledExecutionResult], Awaitable[None]],
        *,
        clock: Callable[[], float] = time_module.time,
        timezone_for: Callable[[ScheduledTask], str | Awaitable[str]] | None = None,
        expire_approval: Callable[[str, float], Awaitable[str]] | None = None,
        expire_authorization: Callable[[str, float], Awaitable[str]] | None = None,
        poll_interval: float = 5,
        grace_seconds: float = 60,
        lease_seconds: float = 60,
    ) -> None:
        self.store, self.executor, self.notifier = store, executor, notifier
        self.clock, self.timezone_for = clock, timezone_for
        self.expire_approval, self.expire_authorization = expire_approval, expire_authorization
        self.poll_interval = _number(poll_interval, "poll_interval")
        self.grace_seconds = _number(grace_seconds, "grace_seconds")
        self.lease_seconds = _number(lease_seconds, "lease_seconds")
        if self.poll_interval <= 0 or self.grace_seconds < 0 or self.lease_seconds < 1:
            raise ValueError("poll_interval and lease_seconds must be positive and grace_seconds nonnegative")
        self._worker: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._timezone_tasks: set[asyncio.Task[Any]] = set()
        self._timezone_cursor = ""

    async def run_once(self) -> ScheduledRun | None:
        now = _number(self.clock(), "clock")
        self.store.expire_runs(now=now)
        self.store.expire_deliveries(now=now)
        for pending in self.store.pending_deliveries():
            task = self.store.get(pending.task_id)
            if task is None:
                logger.warning("scheduled notice has missing task %s", pending.task_id)
                continue
            result = ScheduledExecutionResult(pending.status, pending.summary)  # type: ignore[arg-type]
            return await self._deliver(task, pending, result)
        if self.expire_approval is not None or self.expire_authorization is not None:
            for waiting in self.store.expired_waits(now=now):
                expire = self.expire_approval if waiting.wait_kind == "approval" else self.expire_authorization
                if expire is None:
                    continue
                outcome = await expire(waiting.wait_id, now)  # type: ignore[arg-type]
                if outcome in ("not_due", "executing"):
                    continue
                if outcome not in ("expired", "unknown", "missing"):
                    raise ValueError("invalid approval expiry result")
                status = "failed" if outcome == "expired" else "unknown"
                try:
                    waiting = self.store.finish_run(
                        waiting.run_id,
                        status,
                        now=now,
                        summary="continuation expired" if status == "failed" else "continuation outcome unknown",
                        expected_status=waiting.status,
                        reset_delivery=True,
                    )
                except ScheduledRunConflict:
                    continue
                task = self.store.get(waiting.task_id)
                if task is None:
                    raise RuntimeError("scheduled task is missing")
                result = ScheduledExecutionResult(status, waiting.summary)  # type: ignore[arg-type]
                return await self._deliver(task, waiting, result)
        if self.timezone_for is not None:
            await self._refresh_timezones(now)
        run = self.store.claim_due(now=now, grace_seconds=self.grace_seconds, lease_seconds=self.lease_seconds)
        if run is None:
            return None
        task = self.store.get(run.task_id)
        if task is None:
            raise RuntimeError("claimed scheduled task is missing")
        if run.status == "running":
            execution = asyncio.ensure_future(self.executor(task, run))
            lease_lost = asyncio.Event()
            heartbeat = asyncio.create_task(self._heartbeat(run, execution, lease_lost))
            try:
                result = await execution
                if not isinstance(result, ScheduledExecutionResult) or result.status in ("missed", "skipped_overlap"):
                    raise ValueError("executor returned invalid result")
            except asyncio.CancelledError:
                try:
                    self.store.finish_run(
                        run.run_id,
                        "unknown",
                        now=self.clock(),
                        summary="execution interrupted",
                        expected_status="running",
                        expected_token=run.lease_token,
                    )
                except ScheduledRunConflict:
                    self.store.expire_runs(now=self.clock())
                if lease_lost.is_set():
                    return self.store.get_run(run.run_id)
                raise
            except Exception:
                logger.warning("scheduled execution failed with unknown outcome for run %s", run.run_id)
                result = ScheduledExecutionResult("unknown", "execution outcome unknown")
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            try:
                run = self.store.finish_run(
                    run.run_id,
                    result.status,
                    now=self.clock(),
                    summary=result.summary,
                    expected_status="running",
                    expected_token=run.lease_token,
                )
            except ScheduledRunConflict:
                # A fast approval/OAuth callback can advance the run before
                # this initial turn returns. Never overwrite or redeliver it.
                self.store.expire_runs(now=self.clock())
                return self.store.get_run(run.run_id)
        else:
            result = ScheduledExecutionResult(run.status)  # type: ignore[arg-type]
        return await self._deliver(task, run, result)

    async def _refresh_timezones(self, now: float) -> None:
        if self.timezone_for is None:
            return
        begin_cycle = getattr(self.timezone_for, "begin_cycle", None)
        if callable(begin_cycle):
            begin_cycle()
        semaphore = asyncio.Semaphore(4)

        async def resolve(task: ScheduledTask) -> tuple[str, str]:
            async with semaphore:
                try:
                    zone = self.timezone_for(task)  # type: ignore[misc]
                    if inspect.isawaitable(zone):
                        zone = await zone
                    ZoneInfo(zone)
                    return task.task_id, zone
                except Exception:  # noqa: BLE001 - preserve stored timezone on lookup failure
                    return task.task_id, task.timezone

        tasks, self._timezone_cursor = self.store.active_calendar_batch(cursor=self._timezone_cursor)
        pending = {asyncio.create_task(resolve(task)) for task in tasks}
        if not pending:
            return
        self._timezone_tasks.update(pending)
        for future in pending:
            future.add_done_callback(self._timezone_tasks.discard)
        try:
            done, remaining = await asyncio.wait(pending, timeout=2)
        except asyncio.CancelledError:
            for future in pending:
                future.cancel()
            raise
        for future in remaining:
            future.cancel()
        zones = dict(future.result() for future in done if not future.cancelled() and future.exception() is None)
        if zones:
            self.store.refresh_timezones(
                lambda task: zones.get(task.task_id, task.timezone), now=now, task_ids=tuple(zones)
            )

    async def _heartbeat(self, run: ScheduledRun, execution: asyncio.Future[Any], lease_lost: asyncio.Event) -> None:
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            try:
                active = self.store.renew_lease(
                    run.run_id, run.lease_token, now=self.clock(), lease_seconds=self.lease_seconds
                )
            except Exception:  # noqa: BLE001 - stop work if durable fencing is unavailable
                active = False
            if not active:
                lease_lost.set()
                execution.cancel()
                return

    async def _deliver(
        self, task: ScheduledTask, run: ScheduledRun, result: ScheduledExecutionResult
    ) -> ScheduledRun | None:
        token = self.store.claim_delivery(run.run_id, now=self.clock())
        if token is None:
            return self.store.get_run(run.run_id)
        try:
            await self.notifier(task, run, result)
        except asyncio.CancelledError:
            with suppress(ValueError):
                self.store.record_delivery(run.run_id, "unknown", token=token, now=self.clock())
            raise
        except Exception:
            logger.warning("scheduled notification outcome unknown for run %s", run.run_id)
            try:
                return self.store.record_delivery(run.run_id, "unknown", token=token, now=self.clock())
            except ValueError:
                return self.store.get_run(run.run_id)
        try:
            return self.store.record_delivery(run.run_id, "delivered", token=token, now=self.clock())
        except ValueError:
            return self.store.get_run(run.run_id)

    async def start(self) -> None:
        if self._worker is not None and not self._worker.done():
            raise RuntimeError("scheduled runner already started")
        self._stopping.clear()
        self._worker = asyncio.create_task(self._run_loop())

    async def aclose(self, *, deadline: float | None = None) -> bool:
        deadline = deadline if deadline is not None else asyncio.get_running_loop().time() + 30
        if self._worker is not None:
            worker = self._worker
            self._stopping.set()
            if worker.done():
                await asyncio.gather(worker, return_exceptions=True)
                self._worker = None
                return True
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            try:
                await asyncio.wait_for(asyncio.shield(worker), timeout=remaining)
            except asyncio.TimeoutError:
                worker.cancel()
                await asyncio.sleep(0)
                if not worker.done():
                    return False
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if worker.cancelled() and not getattr(current, "cancelling", lambda: 0)():
                    self._worker = None
                    return True
                worker.cancel()
                raise
            self._worker = None
        pending = {task for task in self._timezone_tasks if not task.done()}
        for task in pending:
            task.cancel()
        if pending:
            _done, pending = await asyncio.wait(pending, timeout=max(0.0, deadline - asyncio.get_running_loop().time()))
            if pending:
                return False
        return True

    async def _run_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                result = await self.run_once()
                if result is None:
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("scheduled runner iteration failed")
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_interval)


class ScheduledAgentAdapter:
    """Connect a scheduled worker to an AgentEngine without duplicating its replies."""

    def __init__(self, engine: Any) -> None:
        if getattr(engine, "scheduled_store", None) is None:
            raise ValueError("agent needs its persistent scheduled_store")
        self.engine = engine

    async def execute(self, task: ScheduledTask, run: ScheduledRun) -> ScheduledExecutionResult:
        from ..auth import user_from_identity_keys
        from .replies import ScheduledTurn

        turn = ScheduledTurn(
            chat_id=task.chat_id,
            tenant_key=task.tenant_key,
            user=user_from_identity_keys(task.owner_keys),
            session_id=run.session_id,
            run_id=run.run_id,
            policy=cast(Literal["read_only", "confirm_writes"], task.policy),
            timezone=task.timezone,
            lease_token=run.lease_token,
        )
        return ScheduledExecutionResult(await self.engine.run_scheduled(turn, task.instruction))

    async def notify(self, task: ScheduledTask, run: ScheduledRun, result: ScheduledExecutionResult) -> None:
        # The engine already sends answers and authorization/approval cards.
        # Only events not delivered by the engine need a separate receipt.
        text = {
            "missed": "定时任务错过了执行时间，未自动补跑。",
            "skipped_overlap": "上次定时任务尚未结束，已跳过本次执行。",
            "unknown": "定时任务执行结果未知，请先核对后再处理。",
        }.get(result.status)
        if result.status == "failed" and result.summary == "continuation expired":
            text = "定时任务的确认或授权已过期，本次未继续执行。"
        if text is not None:
            if self.engine.client is None:
                raise RuntimeError("agent has no notification client")
            await self.engine.client.im.send(task.chat_id, text, msg_type="text", receive_id_type="chat_id")


def _task(row: sqlite3.Row) -> ScheduledTask:
    return ScheduledTask(
        row["task_id"],
        row["tenant_key"],
        tuple(json.loads(row["owner_keys"])),
        row["chat_id"],
        row["instruction"],
        Schedule.from_dict(json.loads(row["schedule"])),
        row["policy"],
        row["timezone"],
        row["state"],
        row["next_run_at"],
        row["revision"],
    )


def _run(row: sqlite3.Row) -> ScheduledRun:
    return ScheduledRun(
        row["run_id"],
        row["task_id"],
        row["scheduled_for"],
        row["status"],
        row["session_id"],
        row["started_at"],
        row["finished_at"],
        row["summary"],
        row["delivery_status"],
        row["delivery_deadline"],
        row["wait_kind"],
        row["wait_id"],
        row["wait_deadline"],
        row["lease_token"],
        row["lease_until"],
    )
