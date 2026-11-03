# OpenFeishu
# Copyright (C) 2024-Present  DanLing
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Optional, SQLite-backed intake for Agent events and continuations.

All callers of this inbox must use the same durable work envelope. Public
message payloads are wrapped as data and cannot select a continuation kind.
Only a verified Feishu callback may invoke its private continuation methods.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Mapping
from typing import Any

from .._sqlite import run_transaction
from ..auth import SqliteOAuthTokenStore, user_from_identity_keys, user_identity_keys
from ..events.envelope import Event
from ..events.jobs import (
    SqliteEventJobStore,
    current_event_job,
    mark_current_event_side_effect_started,
)
from .persistence import (
    SqlitePendingApprovalStore,
    SqlitePendingAuthorizationStore,
    approval_from_dict,
    authorization_from_dict,
)
from .session import (
    SCHEDULED_APPROVAL_TTL_SECONDS,
    SCHEDULED_AUTHORIZATION_TTL_SECONDS,
    PendingApproval,
    _scheduled_approval,
    _scheduled_authorization,
)


def _id(kind: str, value: str) -> str:
    return "agent:" + kind + ":" + hashlib.sha256(value.encode()).hexdigest()


def _same_file(*connections: Any) -> bool:
    paths: list[str] = []
    for db in connections:
        if not isinstance(db, sqlite3.Connection):
            return False
        row = db.execute("PRAGMA database_list").fetchone()
        if not row or not row[2]:
            return False
        paths.append(str(row[2]))
    try:
        return all(os.path.samefile(paths[0], path) for path in paths[1:])
    except OSError:
        return False


class SqliteDurableCallbacks:
    """Atomically accept OAuth continuations and serialize Agent work by session.

    All three stores must share one on-disk SQLite database. The host must run
    an EventJobRunner with ``payload_decoder=lambda value: value`` and the
    ``dispatch`` method, and pass this object as the receiver's job store.
    Construct the inbox before accepting events, after old-format jobs drain.
    """

    def __init__(
        self,
        jobs: SqliteEventJobStore,
        tokens: SqliteOAuthTokenStore,
        authorizations: SqlitePendingAuthorizationStore,
        *,
        approvals: SqlitePendingApprovalStore | None = None,
        interrupt_private: bool = False,
    ) -> None:
        if not isinstance(jobs, SqliteEventJobStore) or not isinstance(tokens, SqliteOAuthTokenStore):
            raise ValueError("durable callbacks require SQLite job and token stores")
        if not isinstance(authorizations, SqlitePendingAuthorizationStore) or not _same_file(
            jobs._db, tokens._db, authorizations._db
        ):
            raise ValueError("durable callback stores must use the same on-disk SQLite database")
        if approvals is not None and (
            not isinstance(approvals, SqlitePendingApprovalStore) or not _same_file(jobs._db, approvals._db)
        ):
            raise ValueError("durable approval store must use the same on-disk SQLite database")
        if not isinstance(interrupt_private, bool):
            raise ValueError("interrupt_private must be a boolean")
        if interrupt_private and jobs._unknown_ttl is not None:
            raise ValueError("private interruption requires UNKNOWN records without automatic expiry")
        self.jobs, self.tokens, self.authorizations = jobs, tokens, authorizations
        self.approvals = approvals
        self.interrupt_private = interrupt_private

        def initialize() -> None:
            # A durable worker cannot safely interpret legacy/unrelated work.
            # Older producers must be stopped before this one-time check.
            for (payload,) in self.jobs._db.execute("SELECT payload FROM event_jobs WHERE status != 'completed'"):
                try:
                    item = json.loads(payload)
                except (TypeError, ValueError) as exc:
                    raise ValueError("drain old event jobs before enabling durable callbacks") from exc
                if (
                    not isinstance(item, dict)
                    or type(item.get("agent_work")) is not int
                    or item["agent_work"] != 1
                    or item.get("kind") not in ("event", "approval", "authorization")
                    or not isinstance(item.get("data"), dict)
                ):
                    raise ValueError("drain old event jobs before enabling durable callbacks")
            self.jobs._db.execute(
                "CREATE TABLE IF NOT EXISTS agent_oauth_receipts "
                "(state_hash TEXT PRIMARY KEY, authorization_id TEXT NOT NULL, tenant_key TEXT NOT NULL, "
                "status TEXT NOT NULL, created_at REAL NOT NULL)"
            )

        run_transaction(self.jobs._db, initialize)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.jobs, name)

    async def enqueue(
        self, event_id: str, payload: dict[str, Any], *, partition_key: str, now: float | None = None
    ) -> bool:
        if not event_id or not partition_key:
            raise ValueError("event and session identities are required")
        data = json.loads(json.dumps(payload, ensure_ascii=False))
        data.pop("token", None)
        header = data.get("header")
        if isinstance(header, dict):
            header.pop("token", None)
        event = data.get("event")
        if isinstance(event, dict):
            event.pop("token", None)
        parsed = Event.from_payload(data)
        message = parsed.body.get("message") or {}
        sender = parsed.body.get("sender") or {}
        if not isinstance(message, Mapping) or not isinstance(sender, Mapping):
            raise ValueError("invalid message event")
        if (
            self.interrupt_private
            and parsed.event_type == "im.message.receive_v1"
            and str(sender.get("sender_type") or "").strip().lower() == "app"
        ):
            return False  # AgentEngine ignores app-sent messages; do not let them interrupt or block the lane.
        private_message = (
            self.interrupt_private
            and parsed.event_type == "im.message.receive_v1"
            and str(message.get("chat_type") or "").strip().lower() == "p2p"
        )
        work = {"agent_work": 1, "kind": "event", "data": data}
        if private_message:
            return await self.jobs.enqueue(
                _id("event", event_id), work, partition_key=partition_key, now=now, private_message=True
            )
        return await self.jobs.enqueue(_id("event", event_id), work, partition_key=partition_key, now=now)

    async def begin_authorization(self, raw_state: str, state: Any) -> str:
        """Reserve a signed state before its one-use code is exchanged."""
        authorization_id = state.extra.get("authorization_id")
        if not state.tenant_key or not state.user_keys or not state.nonce or not authorization_id:
            raise PermissionError("durable authorization requires tenant, owner and pending request")
        state_hash = hashlib.sha256(raw_state.encode()).hexdigest()
        async with self.jobs._lock:

            def begin() -> str:
                row = self.jobs._db.execute(
                    "SELECT authorization_id, tenant_key, status FROM agent_oauth_receipts WHERE state_hash=?",
                    (state_hash,),
                ).fetchone()
                if row:
                    if row[0] != authorization_id or row[1] != state.tenant_key:
                        raise PermissionError("authorization receipt identity mismatch")
                    return "completed" if row[2] == "completed" else "uncertain"
                self._pending_authorization(authorization_id, state.tenant_key, state.user_keys)
                self.jobs._db.execute(
                    "INSERT INTO agent_oauth_receipts VALUES (?, ?, ?, 'exchanging', ?)",
                    (state_hash, authorization_id, state.tenant_key, time.time()),
                )
                return "started"

            return run_transaction(self.jobs._db, begin)

    def _pending_authorization(self, authorization_id: str, tenant: str, owner_keys: Any) -> Any:
        row = self.jobs._db.execute(
            "SELECT state, created_at, data FROM authorizations WHERE authorization_id=?", (authorization_id,)
        ).fetchone()
        if not row or row[0] != "awaiting_authorization":
            raise PermissionError("pending authorization is unavailable")
        pending = authorization_from_dict(json.loads(row[2]))
        ttl = SCHEDULED_AUTHORIZATION_TTL_SECONDS if _scheduled_authorization(pending) else self.authorizations._ttl
        if (
            pending.tenant_key != tenant
            or not pending.session_id
            or not pending.owner_user_keys
            or set(pending.owner_user_keys) != set(owner_keys)
            or (row[1] is not None and time.time() >= row[1] + ttl)
        ):
            raise PermissionError("pending authorization identity mismatch")
        return pending

    async def commit_authorization(
        self, raw_state: str, state: Any, token_data: Mapping[str, Any], user: Mapping[str, Any]
    ) -> None:
        """Commit the validated tenant token and resume job in one transaction."""
        authorization_id = state.extra.get("authorization_id")
        state_hash = hashlib.sha256(raw_state.encode()).hexdigest()
        if user.get("tenant_key") != state.tenant_key or not set(user_identity_keys(user)).intersection(
            state.user_keys
        ):
            raise PermissionError("authorized user does not match signed state")
        async with self.jobs._lock:

            def commit() -> None:
                receipt = self.jobs._db.execute(
                    "SELECT status FROM agent_oauth_receipts "
                    "WHERE state_hash=? AND authorization_id=? AND tenant_key=?",
                    (state_hash, authorization_id, state.tenant_key),
                ).fetchone()
                if not receipt or receipt[0] != "exchanging":
                    raise PermissionError("authorization exchange was not reserved")
                pending = self._pending_authorization(authorization_id, state.tenant_key, state.user_keys)
                self.tokens._save_for_tenant_in_transaction(self.jobs._db, state.tenant_key, token_data, user_info=user)
                identity = user_from_identity_keys(tuple(user_identity_keys(user)))
                identity["tenant_key"] = state.tenant_key
                payload = {
                    "agent_work": 1,
                    "kind": "authorization",
                    "data": {
                        "authorization_id": authorization_id,
                        "user": identity,
                    },
                }
                if not self.jobs._enqueue_in_transaction(
                    _id("authorization", authorization_id), payload, partition_key=pending.session_id, now=time.time()
                ):
                    raise ValueError("authorization continuation already accepted")
                self.jobs._db.execute(
                    "UPDATE agent_oauth_receipts SET status='completed' WHERE state_hash=? AND status='exchanging'",
                    (state_hash,),
                )

            run_transaction(self.jobs._db, commit)

    async def dispatch_approval(
        self, event: Event, approval: PendingApproval, *, tenant_key: str | None = None
    ) -> None:
        """Persist a verified card decision using its stored session partition."""
        from ..cards.callback import parse_action
        from .approval import action_value
        from .context import ToolContext

        value = action_value(event)
        decision = value.get("decision")
        approval_store = self.approvals
        if approval_store is None:
            raise RuntimeError("a durable approval store is required")
        if decision not in ("approve", "reject") or value.get("__approval__") != approval.approval_id:
            raise ValueError("invalid approval decision")
        job_id = _id("approval", approval.approval_id)
        actor = ToolContext(event=event).requesting_user()
        actor_keys = set(user_identity_keys(actor))
        tenant_key = tenant_key or event.tenant_key
        if event.tenant_key and event.tenant_key != tenant_key:
            raise PermissionError("approval callback tenant conflicts with trusted tenant")
        async with self.jobs._lock:

            def accept() -> None:
                row = self.jobs._db.execute(
                    "SELECT state, created_at, data FROM approvals WHERE approval_id=?", (approval.approval_id,)
                ).fetchone()
                if not row or row[0] != "awaiting_confirmation":
                    raise PermissionError("approval is unavailable")
                current = approval_from_dict(json.loads(row[2]))
                scheduled = _scheduled_approval(current)
                if (
                    scheduled and row[1] is not None and time.time() >= row[1] + SCHEDULED_APPROVAL_TTL_SECONDS
                ) or approval_store._is_expired(row[0], row[1], scheduled=scheduled):
                    raise PermissionError("approval is expired")
                if (
                    not current.tenant_key
                    or current.tenant_key != tenant_key
                    or not actor_keys.intersection(current.owner_user_keys)
                    or not current.payload_sha256
                    or value.get("payload_sha256") != current.payload_sha256
                    or not current.extra.get("progress_message_id")
                    or parse_action(event).message_id != current.extra.get("progress_message_id")
                    or current.session_id != approval.session_id
                    or current.payload_sha256 != approval.payload_sha256
                ):
                    raise PermissionError("approval callback does not match its pending request")
                # Persist only the fields needed to revalidate and execute a
                # decision, never Feishu's ephemeral callback token.
                safe_event = {
                    "schema": "2.0",
                    "header": {
                        "event_type": "card.action.trigger",
                        "event_id": event.event_id,
                        "tenant_key": tenant_key,
                    },
                    "event": {
                        "operator": user_from_identity_keys(tuple(actor_keys)),
                        "action": {
                            "value": {
                                "__approval__": current.approval_id,
                                "decision": decision,
                                "payload_sha256": current.payload_sha256,
                            }
                        },
                        "context": {"open_message_id": parse_action(event).message_id, "open_chat_id": current.chat_id},
                    },
                }
                payload = {
                    "agent_work": 1,
                    "kind": "approval",
                    "data": {
                        "event": safe_event,
                        "decision": decision,
                        "payload_sha256": current.payload_sha256,
                    },
                }
                if self.jobs._enqueue_in_transaction(
                    job_id, payload, partition_key=current.session_id, now=time.time()
                ):
                    return
                previous = self.jobs._get_locked(job_id)
                if not previous or previous.partition_key != current.session_id:
                    raise ValueError("approval receipt unavailable")
                data = previous.payload.get("data") or {}
                if data.get("decision") != decision or data.get("payload_sha256") != current.payload_sha256:
                    raise ValueError("approval decision conflicts with an accepted receipt")

            run_transaction(self.jobs._db, accept)

    def card_handler(self, agent: Any) -> Any:
        """Create a verified, durable card callback handler for an AgentEngine."""
        from .approval import handle_card_action

        async def receive(event: Event) -> dict[str, Any]:
            async def persist(callback: Event, approval: PendingApproval) -> None:
                tenant = agent._tool_context(callback).resolved_tenant_key()
                await self.dispatch_approval(callback, approval, tenant_key=tenant)

            return await handle_card_action(agent, event, decision_dispatcher=persist)

        return receive

    def runner(self, agent: Any, *, worker_id: str) -> Any:
        """Build a worker for an assembled Agent; the host owns its lifecycle."""
        from ..events.job_runner import EventJobRunner

        dispatcher = agent.dispatcher()

        async def handle(payload: dict[str, Any]) -> Any:
            return await self.dispatch(payload, agent=agent.engine, dispatcher=dispatcher)

        return EventJobRunner(
            self.jobs,
            handle,
            worker_id=worker_id,
            payload_decoder=lambda payload: payload,
            protected=self.interrupt_private,
        )

    async def dispatch(self, payload: dict[str, Any], *, agent: Any, dispatcher: Any) -> Any:
        """Execute an internally-created work envelope in a durable job runner."""
        if payload.get("agent_work") != 1 or not isinstance(payload.get("data"), dict):
            raise ValueError("invalid durable Agent work envelope")
        if self.interrupt_private:
            from .event_session import fenced_event_session_scope

            context = current_event_job()
            if context is None or not context.lease_token or not context.partition_key:
                raise RuntimeError("protected Agent work requires a fenced event lease")
            with fenced_event_session_scope(agent._store, context, context.partition_key):
                return await self._dispatch(payload, agent=agent, dispatcher=dispatcher)
        return await self._dispatch(payload, agent=agent, dispatcher=dispatcher)

    async def _dispatch(self, payload: dict[str, Any], *, agent: Any, dispatcher: Any) -> Any:
        kind, data = payload.get("kind"), payload["data"]
        if kind == "event":
            return await dispatcher.dispatch(Event.from_payload(data), propagate_errors=True, skip_seen_store=True)
        if kind == "approval":
            from .approval import process_card_action

            return await process_card_action(agent, Event.from_payload(data["event"]))
        if kind == "authorization":
            from .oauth import resume_authorization

            result = await resume_authorization(
                agent, data["authorization_id"], user=data["user"], propagate_errors=True
            )
            if result == "already_claimed":
                await mark_current_event_side_effect_started()
                raise RuntimeError("authorization execution requires reconciliation")
            return result
        raise ValueError("unsupported durable Agent work kind")
