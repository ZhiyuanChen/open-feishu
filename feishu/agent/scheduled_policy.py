# OpenFeishu
# Copyright (C) 2024-Present  DanLing
#
# This file is part of OpenFeishu.
#
# OpenFeishu is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Fail-closed tool access for scheduled executions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, Callable, Literal

from ..auth import user_identity_keys
from .context import current_tool_context
from .integrity import payload_sha256
from .llm import ToolSpec
from .result import ToolResult
from .session import PendingApproval, PendingAuthorization
from .tools import Tool, ToolRegistry


class ScheduledToolBlocked(PermissionError):
    """A tool cannot run under the scheduled task's policy."""


class ScheduledContinuationNotReady(RuntimeError):
    """The original scheduled turn has not finished persisting its wait."""


class ScheduledDeliveryUnknown(RuntimeError):
    """A scheduled card may have been delivered, but its outcome is unknown."""


@dataclass
class _ApprovalGrant:
    approval_id: str
    name: str
    digest: str
    owners: frozenset[str]
    ready: bool = False
    used: bool = False


_GRANT: ContextVar[_ApprovalGrant | None] = ContextVar("scheduled_tool_approval", default=None)
_SCOPED_REGISTRY: ContextVar[tuple[Any, ScheduledPolicyRegistry] | None] = ContextVar(
    "scheduled_tool_registry", default=None
)
_SCOPED_LEASE: ContextVar[tuple[str, str] | None] = ContextVar("scheduled_run_lease", default=None)


def current_scheduled_lease(run_id: str) -> str | None:
    scoped = _SCOPED_LEASE.get()
    return scoped[1] if scoped is not None and scoped[0] == run_id else None


def assert_scheduled_context_lease(agent: Any) -> None:
    from .replies import ScheduledTurn

    turn = current_tool_context().event
    if not isinstance(turn, ScheduledTurn):
        raise PermissionError("scheduled tool context is missing")
    agent._assert_scheduled_lease(turn)


@contextmanager
def scheduled_lease_scope(run_id: str, token: str) -> Iterator[None]:
    if not run_id or not token:
        raise ValueError("scheduled run and lease token are required")
    reset = _SCOPED_LEASE.set((run_id, token))
    try:
        yield
    finally:
        _SCOPED_LEASE.reset(reset)


def current_scheduled_registry(registry: Any) -> Any:
    """Return the registry scoped to this turn, without affecting other tasks."""
    scoped = _SCOPED_REGISTRY.get()
    return scoped[1] if scoped is not None and scoped[0] is registry else registry


@contextmanager
def scheduled_registry_scope(
    registry: Any, policy: Literal["read_only", "confirm_writes"], *, lease_validator: Callable[[], None] | None = None
) -> Iterator[None]:
    """Apply a scheduled policy only to calls made by the current async task."""
    if isinstance(registry, ScheduledPolicyRegistry):
        if registry.policy != policy:
            raise ValueError("cannot replace an active scheduled policy")
        registry = registry._registry
    token = _SCOPED_REGISTRY.set((registry, ScheduledPolicyRegistry(registry, policy, lease_validator=lease_validator)))
    try:
        yield
    finally:
        _SCOPED_REGISTRY.reset(token)


def _stored_policy(pending: PendingApproval | PendingAuthorization) -> Literal["read_only", "confirm_writes"] | None:
    """Recover the policy for scheduled continuations and reject missing metadata."""
    session = pending.session_id.split("::", 1)[-1]
    if not session.startswith("scheduled:"):
        return None
    policy = pending.extra.get("scheduled_policy")
    run_id = pending.extra.get("scheduled_run_id")
    if policy not in ("read_only", "confirm_writes") or not run_id or session.rsplit(":", 1)[-1] != run_id:
        raise ValueError("scheduled continuation is missing its run or tool policy")
    return policy


def _validate_scheduled_pending(
    agent: Any, pending: PendingApproval | PendingAuthorization
) -> Literal["read_only", "confirm_writes"] | None:
    """Check a scheduled continuation against its authoritative task and run."""
    policy = _stored_policy(pending)
    if policy is None:
        return None
    store = getattr(agent, "scheduled_store", None)
    if store is None:
        raise RuntimeError("scheduled continuation has no persistent scheduled store")
    run = store.get_run(pending.extra["scheduled_run_id"])
    task = store.get(run.task_id) if run is not None else None
    kind = "approval" if isinstance(pending, PendingApproval) else "authorization"
    pending_id = pending.approval_id if isinstance(pending, PendingApproval) else pending.authorization_id
    if (
        run is None
        or task is None
        or run.session_id != pending.session_id
        or run.status not in ("running", "awaiting_authorization", "awaiting_confirmation")
        or run.wait_kind != kind
        or run.wait_id != pending_id
        or task.state in ("paused", "deleted")
        or task.chat_id != pending.chat_id
        or not task.tenant_key
        or task.tenant_key != pending.tenant_key
        or task.policy != policy
        or set(task.owner_keys) != set(pending.owner_user_keys)
    ):
        raise PermissionError("scheduled continuation does not match its active task and run")
    expected = "awaiting_confirmation" if isinstance(pending, PendingApproval) else "awaiting_authorization"
    if run.status != expected:
        token = current_scheduled_lease(run.run_id)
        if run.status != "running" or not token:
            raise ScheduledContinuationNotReady("scheduled turn is still preparing its continuation")
        store.validate_lease(run.run_id, token, now=agent._now())
    return policy


@asynccontextmanager
async def scheduled_resume_lease(agent: Any, pending: PendingApproval | PendingAuthorization) -> AsyncIterator[None]:
    """Claim and renew the run while handling a persisted continuation."""
    policy = _stored_policy(pending)
    if policy is None:
        yield
        return
    run_id = pending.extra["scheduled_run_id"]
    wait_id = pending.approval_id if isinstance(pending, PendingApproval) else pending.authorization_id
    expected = "awaiting_confirmation" if isinstance(pending, PendingApproval) else "awaiting_authorization"
    run = agent.scheduled_store.claim_resume(run_id, wait_id, expected_status=expected, now=agent._now())
    if not run.lease_token:
        raise RuntimeError("scheduled continuation did not receive a lease")
    owner_task = asyncio.current_task()

    async def heartbeat() -> None:
        try:
            while True:
                await asyncio.sleep(20)
                if not agent.scheduled_store.renew_lease(run_id, run.lease_token, now=agent._now()):
                    current = agent.scheduled_store.get_run(run_id)
                    if (
                        current is not None
                        and current.lease_token == run.lease_token
                        and current.status in ("succeeded", "failed", "awaiting_authorization", "awaiting_confirmation")
                    ):
                        return
                    if owner_task is not None:
                        owner_task.cancel()
                    return
        except Exception:
            if owner_task is not None:
                owner_task.cancel()

    renewal = asyncio.create_task(heartbeat())
    try:
        with scheduled_lease_scope(run_id, run.lease_token):
            yield
    finally:
        renewal.cancel()
        await asyncio.gather(renewal, return_exceptions=True)


@contextmanager
def _verified_approval(approval: PendingApproval) -> Iterator[None]:
    """Bind a validated card to this task; atomic claim still has to succeed."""
    grant = None
    if approval.owner_user_keys and approval.payload_sha256 == payload_sha256(approval.arguments):
        grant = _ApprovalGrant(
            approval.approval_id, approval.tool_name, approval.payload_sha256, frozenset(approval.owner_user_keys)
        )
    token = _GRANT.set(grant)
    try:
        yield
    finally:
        _GRANT.reset(token)


@contextmanager
def _claimed_approval(approval: PendingApproval) -> Iterator[None]:
    """Enable the grant only during execution of the successfully claimed card."""
    grant = _GRANT.get()
    matches = bool(
        grant
        and grant.approval_id == approval.approval_id
        and grant.name == approval.tool_name
        and grant.digest == approval.payload_sha256 == payload_sha256(approval.arguments)
        and grant.owners == frozenset(approval.owner_user_keys)
    )
    if matches and grant:
        grant.ready = True
    try:
        yield
    finally:
        if matches and grant:
            grant.ready = False


@contextmanager
def _prior_approval(authorization: PendingAuthorization) -> Iterator[None]:
    """Bind a claimed OAuth continuation after prior-approval verification."""
    approved = authorization.extra["approved_tool_call"]
    grant = _ApprovalGrant(
        str(approved["approval_id"]),
        authorization.tool_name,
        payload_sha256(authorization.arguments),
        frozenset(authorization.owner_user_keys),
        ready=True,
    )
    token = _GRANT.set(grant)
    try:
        yield
    finally:
        _GRANT.reset(token)


class ScheduledPolicyRegistry:
    """Expose only permitted tools and prevent direct execution of writes.

    The wrapped registry is trusted configuration and must not be exposed to
    the model. ``confirm_writes`` presents non-read tools as requiring approval,
    and permits their dispatch only inside a validated, claimed approval or
    previously approved OAuth continuation.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        policy: Literal["read_only", "confirm_writes"],
        *,
        lease_validator: Callable[[], None] | None = None,
    ) -> None:
        if policy not in ("read_only", "confirm_writes"):
            raise ValueError("unsupported scheduled policy")
        self._registry = registry
        self.policy = policy
        self._lease_validator = lease_validator

    def specs(self) -> list[ToolSpec]:
        return [spec for spec in self._registry.specs() if self._allowed(spec.name)]

    def get(self, name: str) -> Tool:
        tool = self._registry.get(name)
        if self.policy == "read_only" and tool.scheduled_effect != "read":
            raise KeyError(name)
        if tool.scheduled_effect != "read" and not tool.requires_approval:
            return replace(tool, requires_approval=True)
        return tool

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if self._lease_validator is not None:
            self._lease_validator()
        tool = self._registry.get(name)
        if tool.scheduled_effect != "read" or tool.requires_approval:
            grant = _GRANT.get()
            users = set(user_identity_keys(current_tool_context().requesting_user()))
            if (
                (self.policy == "read_only" and tool.scheduled_effect != "read")
                or grant is None
                or not grant.ready
                or grant.used
                or grant.name != name
                or grant.digest != payload_sha256(arguments)
                or not (grant.owners & users)
            ):
                raise ScheduledToolBlocked(f"scheduled execution of {name!r} requires a trusted approval")
            grant.used = True
        return await self._registry.dispatch(name, arguments)

    def _allowed(self, name: str) -> bool:
        return self.policy == "confirm_writes" or self._registry.get(name).scheduled_effect == "read"
