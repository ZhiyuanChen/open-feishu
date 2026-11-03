from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from feishu.agent.integrity import payload_sha256
from feishu.agent.llm import Message, ToolUsePart
from feishu.agent.loop import AgentEngine
from feishu.agent.persistence import SqliteSessionStore
from feishu.agent.scheduled import Schedule, ScheduledTaskStore
from feishu.agent.session import ClaimResult, PendingApproval, PendingAuthorization
from feishu.agent.tools import ToolRegistry
from feishu.events.envelope import Event
from tests._fakes import FakeLlmBackend


@pytest.fixture
def expired_scheduled_wait(tmp_path):
    async def prepare(kind, missing):
        now = time.time()
        store = ScheduledTaskStore(tmp_path / "agent.db")
        registry = ToolRegistry()
        writes = []
        registry.register(
            "write", lambda: writes.append("written"), description="write", input_schema={"type": "object"}
        )
        agent = AgentEngine(
            backend=FakeLlmBackend([]),
            registry=registry,
            store=SqliteSessionStore(tmp_path / "agent.db"),
            scheduled_store=store,
        )
        owner = ("user_id:owner",)
        draft = store.create_draft(
            tenant_key="tenant",
            owner_keys=owner,
            chat_id="chat",
            instruction="check",
            now=now - 120,
            schedule=Schedule.interval(seconds=100, anchor_at=now),
            policy="confirm_writes",
        )
        store.confirm_draft(draft.draft_id, owner, draft.payload_hash, tenant_key="tenant", now=now - 120)
        run = store.claim_due(now=now)
        await agent.store.set(
            run.session_id,
            [
                Message(
                    role="assistant",
                    content=[ToolUsePart(id="call", name="write", arguments={})],
                    continuation_id="origin",
                )
            ],
        )
        fields = {
            "session_id": run.session_id,
            "tool_call_id": "call",
            "tool_name": "write",
            "arguments": {},
            "owner_user_keys": owner,
            "tenant_key": "tenant",
            "chat_id": "chat",
            "created_at": int(now) - (1801 if kind == "approval" else 3601),
            "extra": {
                "scheduled_policy": "confirm_writes",
                "scheduled_run_id": run.run_id,
                "origin_continuation_id": "origin",
            },
        }
        if kind == "approval":
            pending = PendingApproval(approval_id="approval", payload_sha256=payload_sha256({}), **fields)
            await agent.approvals.put(pending)
            wait_id = pending.approval_id
        else:
            pending = PendingAuthorization(authorization_id="authorization", **fields)
            await agent.authorizations.put(pending)
            wait_id = pending.authorization_id
        store.record_wait(run.run_id, kind, wait_id, now - 1, expected_token=run.lease_token, now=now)
        store.finish_run(
            run.run_id,
            "awaiting_confirmation" if kind == "approval" else "awaiting_authorization",
            now=now,
            expected_token=run.lease_token,
        )
        if missing:
            pending_store = agent.approvals if kind == "approval" else agent.authorizations
            pending_store.claim = AsyncMock(return_value=ClaimResult.MISSING)
        event = Event.from_payload(
            {
                "schema": "2.0",
                "header": {"event_type": "card.action.trigger", "tenant_key": "tenant"},
                "event": {
                    "operator": {"user_id": "owner"},
                    "action": {
                        "value": {
                            "__approval__": "approval",
                            "decision": "approve",
                            "payload_sha256": payload_sha256({}),
                        }
                    },
                },
            }
        )
        return agent, store, run, event, writes

    return prepare
