"""Group cards stay in the originating reply tree across durable continuations."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from feishu.agent import approval as approval_flow
from feishu.agent import oauth as oauth_flow
from feishu.agent.context import current_tool_context, use_tool_context
from feishu.agent.integrity import payload_sha256
from feishu.agent.llm import Message, ToolCall, ToolUsePart
from feishu.agent.loop import AgentEngine
from feishu.agent.progress import _ProgressCard
from feishu.agent.result import ToolOutcome, ToolResult
from feishu.agent.session import PendingApproval, PendingAuthorization
from feishu.agent.tools import ToolRegistry
from feishu.events.envelope import Event
from tests._fakes import FakeLlmBackend, text_turn


class RecordingIM:
    def __init__(self, *, fail_patch=False):
        self.sent = []
        self.replied = []
        self.patched = []
        self.fail_patch = fail_patch

    async def send(self, chat_id, content, *, msg_type, receive_id_type):
        self.sent.append((chat_id, content, msg_type, receive_id_type))
        return {"message_id": f"sent-{len(self.sent)}"}

    async def reply(self, message_id, content, *, msg_type="text", reply_in_thread=None, uuid=None):
        self.replied.append((message_id, content, msg_type, reply_in_thread))
        return {"message_id": f"reply-{len(self.replied)}"}

    async def patch(self, message_id, content):
        self.patched.append((message_id, content))
        if self.fail_patch:
            raise RuntimeError("patch unavailable")
        return {"message_id": message_id}


def message_event(*, chat_type="group", message_id="original"):
    return Event.from_payload(
        {
            "header": {"event_type": "im.message.receive_v1", "event_id": "event", "tenant_key": "tenant"},
            "event": {
                "sender": {"sender_id": {"open_id": "owner"}, "sender_type": "user"},
                "message": {
                    "chat_type": chat_type,
                    "chat_id": "chat",
                    "message_id": message_id,
                    "root_id": "root",
                    "parent_id": "parent",
                    "thread_id": "thread",
                    "message_type": "text",
                    "content": json.dumps({"text": "question"}),
                    "untrusted": "do not persist",
                },
            },
        }
    )


def agent_for(im, *, progress=False, handler=None):
    pending_approvals, pending_authorizations = [], []
    registry = ToolRegistry()

    async def write():
        return await handler() if handler else "written"

    registry.register("write", write, description="write", input_schema={"type": "object"}, requires_approval=True)

    def approval_card(pending):
        pending_approvals.append(pending)
        return {"kind": "approval"}

    def authorize_url(_user, _scopes, pending):
        pending_authorizations.append(pending)
        return "https://auth.invalid/authorize"

    agent = AgentEngine(
        backend=FakeLlmBackend([text_turn("done")]),
        registry=registry,
        client=SimpleNamespace(im=im),
        approval_card_builder=approval_card,
        auth_card_builder=lambda _url: {"kind": "authorization"},
        authorize_url_builder=authorize_url,
        progress_card_builder=(lambda _steps, _done, _text: {"kind": "progress"}) if progress else None,
    )
    return agent, pending_approvals, pending_authorizations


@pytest.mark.parametrize("method", ["start", "status", "step"])
@pytest.mark.parametrize("chat_type", ["group", "p2p"])
async def test_first_progress_card_uses_group_reply_tree(method, chat_type):
    im = RecordingIM()
    agent, _, _ = agent_for(im, progress=True)
    progress = _ProgressCard(agent, message_event(chat_type=chat_type))
    if method == "status":
        await progress.update_status("working")
    elif method == "step":
        await progress.step("write")
    else:
        await progress.start()

    if chat_type == "group":
        assert im.sent == []
        assert im.replied == [("original", {"kind": "progress"}, "interactive", None)]
        assert progress.message_id == "reply-1"
    else:
        assert im.replied == []
        assert im.sent == [("chat", {"kind": "progress"}, "interactive", "chat_id")]


@pytest.mark.parametrize("kind", ["approval", "authorization"])
@pytest.mark.parametrize("progress_started", [False, True])
async def test_pending_cards_keep_group_routing_and_replace_progress(kind, progress_started):
    im = RecordingIM()
    agent, approvals, authorizations = agent_for(im, progress=progress_started)
    event = message_event()
    progress = _ProgressCard(agent, event)
    if progress_started:
        await progress.start()
    call = ToolCall(id="call", name="write", arguments="{}")
    with use_tool_context(agent._tool_context(event)):
        if kind == "approval":
            assert await approval_flow.request_approval(agent, event, "profile::branch", [], call, progress)
            pending = approvals[0]
        else:
            assert await oauth_flow.request_authorization(
                agent,
                event,
                "profile::branch",
                [],
                call,
                ToolResult(ToolOutcome.NEEDS_USER_AUTH, auth_scopes=("read",)),
                progress,
            )
            pending = authorizations[0]

    assert pending.session_id == "profile::branch"
    assert pending.owner_user_keys == ("open_id:owner",)
    assert pending.extra["message_context"] == {
        "chat_type": "group",
        "root_id": "root",
        "parent_id": "parent",
        "thread_id": "thread",
    }
    assert im.sent == []
    assert len(im.replied) == 1
    assert im.replied[0][0] == "original"
    if progress_started:
        assert im.patched == [("reply-1", {"kind": kind})]
    else:
        assert im.replied[0][1] == {"kind": kind}


async def test_failed_progress_replacement_replies_with_approval_in_same_tree():
    im = RecordingIM(fail_patch=True)
    agent, _, _ = agent_for(im, progress=True)
    event = message_event()
    progress = _ProgressCard(agent, event)
    await progress.start()
    with use_tool_context(agent._tool_context(event)):
        assert await approval_flow.request_approval(
            agent, event, "branch", [], ToolCall(id="call", name="write", arguments="{}"), progress
        )
    assert im.sent == []
    assert [item[0] for item in im.replied] == ["original", "original"]


async def test_standalone_auth_url_card_replies_to_group_message():
    im = RecordingIM()
    agent, _, _ = agent_for(im)
    assert await oauth_flow.send_auth_card(agent, message_event(), "https://auth.invalid/authorize")
    assert im.sent == []
    assert im.replied == [("original", {"kind": "authorization"}, "interactive", None)]


def test_pending_authorization_restores_only_saved_routing_and_authoritative_ids():
    pending = PendingAuthorization(
        authorization_id="authorization",
        session_id="saved-session",
        tool_call_id="call",
        tool_name="write",
        arguments={},
        chat_id="chat",
        created_message_id="original",
        tenant_key="tenant",
        extra={
            "message_context": {
                "chat_id": "wrong-chat",
                "message_id": "wrong-message",
                "chat_type": "group",
                "root_id": "root",
                "parent_id": {"not": "an id"},
                "thread_id": "thread",
                "sender": "intruder",
            }
        },
    )
    event = oauth_flow.event_from_pending_authorization(pending)
    assert event.body["message"] == {
        "chat_id": "chat",
        "message_id": "original",
        "chat_type": "group",
        "root_id": "root",
        "thread_id": "thread",
    }
    assert event.tenant_key == "tenant"
    assert pending.session_id == "saved-session"


async def test_approval_to_oauth_uses_original_branch_and_owner_not_click_event():
    observed = []

    async def needs_auth():
        context = current_tool_context()
        observed.append((context.requesting_user(), dict(context.event.body["message"])))
        return ToolResult(ToolOutcome.NEEDS_USER_AUTH, auth_scopes=("read",))

    im = RecordingIM()
    agent, _, authorizations = agent_for(im, handler=needs_auth)
    pending = PendingApproval(
        approval_id="approval",
        session_id="trusted-session",
        tool_call_id="call",
        tool_name="write",
        arguments={},
        payload_sha256=payload_sha256({}),
        owner_user_keys=("open_id:owner",),
        tenant_key="tenant",
        chat_id="chat",
        created_message_id="original",
        extra={
            "origin_continuation_id": "origin",
            "message_context": {"chat_type": "group", "root_id": "root", "parent_id": "parent", "thread_id": "thread"},
        },
    )
    await agent.approvals.put(pending)
    history = [
        Message(
            role="assistant", content=[ToolUsePart(id="call", name="write", arguments={})], continuation_id="origin"
        )
    ]
    await agent.store.set(pending.session_id, history)
    await approval_flow._decide_and_resume_locked(
        agent,
        message_event(message_id="wrong-click-message"),
        pending,
        "approve",
        {"payload_sha256": pending.payload_sha256},
        None,
        history,
    )
    assert observed[0][0] == {"open_id": "owner"}
    assert observed[0][1]["root_id"] == "root"
    assert authorizations[0].session_id == "trusted-session"
    assert authorizations[0].tenant_key == "tenant"
    assert authorizations[0].extra["message_context"]["root_id"] == "root"
    assert im.sent == []
    assert im.replied[0][0] == "original"


async def test_oauth_resume_followon_approval_remains_in_original_group_branch():
    im = RecordingIM()
    agent, approvals, _ = agent_for(im)
    pending = PendingAuthorization(
        authorization_id="authorization",
        session_id="trusted-session",
        tool_call_id="call",
        tool_name="write",
        arguments={},
        owner_user_keys=("open_id:owner",),
        tenant_key="tenant",
        chat_id="chat",
        created_message_id="original",
        extra={
            "origin_continuation_id": "origin",
            "message_context": {"chat_type": "group", "root_id": "root", "thread_id": "thread"},
        },
    )
    await agent.authorizations.put(pending)
    await agent.store.set(
        pending.session_id,
        [
            Message(
                role="assistant", content=[ToolUsePart(id="call", name="write", arguments={})], continuation_id="origin"
            )
        ],
    )
    assert (
        await oauth_flow.resume_authorization(
            agent, "authorization", user={"open_id": "intruder", "tenant_key": "tenant"}
        )
        == "forbidden"
    )
    assert approvals == []
    assert (
        await oauth_flow.resume_authorization(agent, "authorization", user={"open_id": "owner", "tenant_key": "tenant"})
        == "resumed"
    )
    assert approvals[0].session_id == "trusted-session"
    assert approvals[0].owner_user_keys == ("open_id:owner",)
    assert approvals[0].extra["message_context"]["root_id"] == "root"
    assert im.sent == []
    assert im.replied[0][0] == "original"


async def test_missing_original_message_id_retains_safe_chat_card_fallback():
    im = RecordingIM()
    agent, _, _ = agent_for(im, progress=True)
    await _ProgressCard(agent, message_event(message_id=None)).start()
    assert im.replied == []
    assert im.sent[0][0] == "chat"
