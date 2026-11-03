# OpenFeishu
# Copyright (C) 2024-Present  DanLing
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Authenticated durable intake and dispatch for non-Feishu events.

The application owns the job store, worker lifecycle, source credentials and
notification mapping store. A successful intake means the event was saved,
not that a handler ran or a Feishu message was delivered.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .._store_io import invoke_store
from ..events.job_runner import EventJobRunner
from ..events.jobs import EventJobStatus, EventJobStore, mark_current_event_side_effect_started
from ..gateway.auth import ServiceAuthError, ServiceCapabilityError, require_service_capability
from ..gateway.config import GatewayConfig
from ..gateway.errors import GatewayRequestError, error_response
from ..gateway.notifications import EventMessageStore, upsert_interactive_card
from .notifications import (
    NotificationCardBuilder,
    TextSummarizer,
    _target,
    build_notification_card,
    notification_summary,
)


@dataclass(frozen=True, slots=True)
class ExternalEvent:
    """Event accepted from an authenticated service; source cannot be client supplied."""

    source: str
    event_id: str
    event_type: str
    data: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ExternalEvent:
        source = _required_string(payload, "source")
        event_id = _required_string(payload, "event_id")
        event_type = _required_string(payload, "event_type")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("data must be an object")
        return cls(source=source, event_id=event_id, event_type=event_type, data=data)

    def as_payload(self) -> dict[str, Any]:
        return {"source": self.source, "event_id": self.event_id, "event_type": self.event_type, "data": self.data}


EventAuthorizer = Callable[[ExternalEvent], bool | Awaitable[bool]]
ExternalEventHandler = Callable[[ExternalEvent], Awaitable[Any]]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExternalEventIntegration:
    """Mount durable external-event intake on ``create_gateway``.

    Set both ``handler`` and a unique ``worker_id`` to run a worker in the
    gateway lifespan. Without them the host must run an ``EventJobRunner``.
    Injected stores, handler and client remain owned by the application.
    """

    store: EventJobStore
    path: str = "/events/external"
    authorize: EventAuthorizer | None = None
    max_body_bytes: int = 1024 * 1024
    handler: ExternalEventHandler | None = None
    worker_id: str | None = None
    poll_seconds: float = 1.0
    shutdown_grace_seconds: float = 30.0

    def __post_init__(self) -> None:
        if (self.handler is None) != (self.worker_id is None):
            raise ValueError("handler and worker_id must be provided together")
        if self.worker_id is not None and not self.worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if not math.isfinite(self.poll_seconds) or self.poll_seconds <= 0:
            raise ValueError("poll_seconds must be finite and positive")
        if not math.isfinite(self.shutdown_grace_seconds) or self.shutdown_grace_seconds <= 0:
            raise ValueError("shutdown_grace_seconds must be finite and positive")

    def routes(self, context: Any) -> list[Route]:
        return [
            create_external_event_route(
                context.config,
                self.store,
                path=self.path,
                authorize=self.authorize,
                max_body_bytes=self.max_body_bytes,
            )
        ]

    @asynccontextmanager
    async def lifespan(self, _context: Any) -> AsyncIterator[None]:
        if self.handler is None or self.worker_id is None:
            yield
            return
        stop = asyncio.Event()
        runner = EventJobRunner(
            self.store, self.handler, worker_id=self.worker_id, payload_decoder=ExternalEvent.from_payload
        )
        task = asyncio.create_task(self._run(runner, stop))
        try:
            yield
        finally:
            stop.set()
            try:
                await asyncio.wait_for(task, timeout=self.shutdown_grace_seconds)
            except asyncio.TimeoutError:
                # wait_for cancels the runner; it settles any in-flight job
                # as retry or UNKNOWN before returning cancellation.
                logger.warning("external event worker exceeded shutdown grace")

    async def _run(self, runner: EventJobRunner, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                job = await runner.run_once()
            except Exception:  # noqa: BLE001 - retain the worker after store outages
                logger.exception("external event worker failed")
                job = None
            if job is not None and job.status in (EventJobStatus.FAILED, EventJobStatus.UNKNOWN):
                logger.error("external event job %s needs reconciliation: %s", job.job_id, job.status.value)
            if job is None:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)


def _key(prefix: str, *values: str) -> str:
    text = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return prefix + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f"{key} must be a non-empty string of at most 512 characters")
    return value


async def _body(request: Request, max_bytes: int) -> dict[str, Any]:
    chunks: list[bytes] = []
    length = 0
    async for chunk in request.stream():
        length += len(chunk)
        if length > max_bytes:
            raise GatewayRequestError("request body too large", 413)
        chunks.append(chunk)
    try:

        def reject_constant(_value: str) -> Any:
            raise ValueError("invalid numeric constant")

        value = json.loads(b"".join(chunks), parse_constant=reject_constant)
    except ValueError as exc:
        raise GatewayRequestError("invalid json") from exc
    if not isinstance(value, dict):
        raise GatewayRequestError("request body must be a JSON object")
    return value


def create_external_event_route(
    config: GatewayConfig,
    store: EventJobStore,
    *,
    path: str = "/events/external",
    authorize: EventAuthorizer | None = None,
    max_body_bytes: int = 1024 * 1024,
) -> Route:
    """Persist authenticated external events before returning 202.

    Each service is a FIFO partition, so events from that service cannot be
    handled out of receipt order or concurrently. Use a store dedicated to
    external events: the default Feishu event runner expects a different
    envelope. Use ``EventJobRunner(..., payload_decoder=ExternalEvent.from_payload)``
    and run/poll that runner as part of the host application's lifecycle.

    Event IDs are unique per service. A replay with different content is a
    conflict. Authorization is checked before acceptance if supplied; handlers
    performing sensitive actions should also enforce authorization at execution.
    """
    if max_body_bytes <= 0:
        raise ValueError("max_body_bytes must be positive")

    async def endpoint(request: Request) -> Response:
        try:
            source = require_service_capability(request, config.service_keys, config.service_capabilities)
            body = await _body(request, max_body_bytes)
            if set(body) - {"event_id", "event_type", "data"}:
                raise GatewayRequestError("unsupported external event fields")
            try:
                event = ExternalEvent.from_payload({**body, "source": source})
            except ValueError as exc:
                raise GatewayRequestError(str(exc)) from exc
            if authorize is not None:
                authorized = authorize(event)
                if inspect.isawaitable(authorized):
                    authorized = await authorized
                if not authorized:
                    raise ServiceCapabilityError("external event denied")
            job_id = _key("external:", source, event.event_id)
            partition = _key("source:", source)
            payload = event.as_payload()
            accepted = await store.enqueue(job_id, payload, partition_key=partition)
            if not accepted:
                prior = await store.get(job_id)
                if prior is None:
                    return error_response("event state unavailable", status_code=503)
                if prior.payload != payload:
                    return error_response("event_id already used with different content", status_code=409)
            return JSONResponse({"accepted": accepted, "job_id": job_id}, status_code=202)
        except ServiceAuthError:
            return error_response("unauthorized", status_code=401)
        except ServiceCapabilityError:
            return error_response("forbidden", status_code=403)
        except GatewayRequestError as exc:
            return error_response(exc.message, status_code=exc.status_code)
        except Exception:  # noqa: BLE001 - failed storage must never be acknowledged
            return error_response("event intake unavailable", status_code=503)

    return Route(path, endpoint, methods=["POST"])


class NotificationEventHandler:
    """Deliver external events as cards to explicitly authorized destinations.

    The ``data`` fields use the regular notification format; ``data.event_id``
    identifies the incident/card, while the outer event ID identifies a single
    delivery. Incident keys are scoped to the authenticated service and target.
    A send or update is marked as an external effect before calling Feishu. An
    ambiguous failure freezes the job as UNKNOWN for operator reconciliation.
    """

    def __init__(
        self,
        client: Any,
        store: EventMessageStore,
        *,
        allowed_targets: Mapping[str, Collection[tuple[str, str]]],
        text_summarizer: TextSummarizer | None = None,
        card_builder: NotificationCardBuilder | None = None,
        max_summary_chars: int = 1200,
    ) -> None:
        self.client = client
        self.store = store
        if not callable(getattr(store, "get_revision", None)) or not callable(getattr(store, "set_revision", None)):
            raise ValueError("notification store must support revision persistence")
        self.allowed_targets = {service: frozenset(targets) for service, targets in allowed_targets.items()}
        self.text_summarizer = text_summarizer
        self.card_builder = card_builder or build_notification_card
        self.max_summary_chars = max_summary_chars

    def authorize(self, event: ExternalEvent) -> bool:
        _notification_revision(event)
        try:
            target_id, target_type = _target(event.data)
        except GatewayRequestError:
            return False
        return (target_type, target_id) in self.allowed_targets.get(event.source, ())

    async def __call__(self, event: ExternalEvent) -> None:
        if not self.authorize(event):
            raise PermissionError("notification target is not authorized")
        target_id, target_type = _target(event.data)
        incident = event.data.get("event_id") or event.event_id
        if not isinstance(incident, str) or not incident.strip():
            raise ValueError("notification event_id must be a non-empty string")
        card_key = _key("external-notification:", event.source, incident, target_type, target_id)
        revision = _notification_revision(event)
        digest = hashlib.sha256(
            json.dumps(
                {"event_type": event.event_type, "data": event.data},
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        previous = await invoke_store(self.store.get_revision, card_key)  # type: ignore[attr-defined]
        if previous is not None:
            previous_revision, previous_digest = previous
            if revision < previous_revision or (revision == previous_revision and digest == previous_digest):
                return
            if revision == previous_revision:
                raise ValueError("notification revision reused with different content")
        summary = await notification_summary(
            event.data, text_summarizer=self.text_summarizer, max_chars=self.max_summary_chars
        )
        card = self.card_builder(event.data, summary)
        await mark_current_event_side_effect_started()
        delivery = await upsert_interactive_card(
            self.client,
            card_key,
            card,
            target_id,
            receive_id_type=target_type,
            store=self.store,
            uuid_prefix="notify-",
        )
        if not delivery.message_id:
            raise RuntimeError("notification response did not contain a message ID")
        await invoke_store(self.store.set_revision, card_key, revision, digest)  # type: ignore[attr-defined]


def _notification_revision(event: ExternalEvent) -> int:
    revision = event.data.get("revision")
    if revision is None and not event.data.get("event_id"):
        return 0  # a one-off event cannot update another incident's card
    if isinstance(revision, bool) or not isinstance(revision, int) or not 0 <= revision <= 2**63 - 1:
        raise GatewayRequestError("notification revision must be a non-negative integer")
    return revision
