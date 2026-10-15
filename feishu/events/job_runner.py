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

"""Execution runner for durable event inbox jobs."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .envelope import Event
from .jobs import (
    _CURRENT_EVENT_JOB,
    EventJob,
    EventJobContext,
    EventJobStore,
)

EventHandler = Callable[[Event], Awaitable[Any]]


class EventJobRunner:
    """Claim, execute, and settle durable events under a renewable lease."""

    def __init__(
        self,
        store: EventJobStore,
        handler: EventHandler,
        *,
        worker_id: str,
        lease_seconds: float = 60.0,
        backoff_base_seconds: float = 1.0,
        backoff_max_seconds: float = 300.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        if not worker_id:
            raise ValueError("worker_id must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if backoff_base_seconds < 0 or backoff_max_seconds < 0:
            raise ValueError("retry backoff values must be non-negative")
        self.store = store
        self.handler = handler
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self._now = now

    async def run_once(self, *, now: float | None = None) -> EventJob | None:
        """Claim and process one job, returning its resulting durable record."""
        current = self._now() if now is None else now
        job = await self.store.claim_next(self.worker_id, now=current, lease_seconds=self.lease_seconds)
        if job is None:
            return None

        context = EventJobContext(
            store=self.store,
            job_id=job.job_id,
            event_id=job.event_id,
            worker_id=self.worker_id,
            side_effect_started=job.side_effect_started,
        )
        token = _CURRENT_EVENT_JOB.set(context)
        handler_task: asyncio.Future[Any] = asyncio.ensure_future(self.handler(Event.from_payload(job.payload)))
        heartbeat_task = asyncio.create_task(self._heartbeat(job))
        try:
            done, _pending = await asyncio.wait((handler_task, heartbeat_task), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat_task in done:
                try:
                    await heartbeat_task
                except Exception:
                    handler_task.cancel()
                    await asyncio.gather(handler_task, return_exceptions=True)
                    raise
            await handler_task
        except asyncio.CancelledError:
            handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)
            await self._settle_failure(job, context, "CancelledError")
            raise
        except Exception as exc:  # noqa: BLE001 - persist the result before returning control to the runtime
            await self._settle_failure(job, context, type(exc).__name__)
        else:
            await self.store.complete(job.job_id, self.worker_id, now=self._now())
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            _CURRENT_EVENT_JOB.reset(token)
        return await self.store.get(job.job_id)

    async def drain(self, limit: int, *, now: float | None = None) -> list[EventJob]:
        """Process up to ``limit`` available jobs in claim order."""
        if limit < 0:
            raise ValueError("limit must be non-negative")
        results: list[EventJob] = []
        for _ in range(limit):
            job = await self.run_once(now=now)
            if job is None:
                break
            results.append(job)
        return results

    async def _heartbeat(self, job: EventJob) -> None:
        interval = max(0.01, self.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            await self.store.renew(
                job.job_id,
                self.worker_id,
                now=self._now(),
                lease_seconds=self.lease_seconds,
            )

    async def _settle_failure(self, job: EventJob, context: EventJobContext, error_type: str) -> None:
        now = self._now()
        if context.side_effect_started:
            await self.store.mark_unknown(job.job_id, self.worker_id, error_type=error_type, now=now)
            return
        exponent = max(0, job.attempt - 1)
        delay = min(self.backoff_max_seconds, self.backoff_base_seconds * (2**exponent))
        await self.store.retry(
            job.job_id,
            self.worker_id,
            available_at=now + delay,
            error_type=error_type,
            now=now,
        )
