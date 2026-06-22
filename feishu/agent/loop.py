# OpenFeishu
# Copyright (C) 2024-Present  DanLing

# This file is part of OpenFeishu.

# OpenFeishu is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.

# OpenFeishu is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# For additional terms and clarifications, please refer to our License FAQ at:
# <https://multimolecule.danling.org/about/license-faq>.

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import uuid4

from ..events.envelope import Event
from .llm import (
    LlmBackend,
    Message,
    MessageStop,
    StopReason,
    StreamChunk,
    TextDelta,
    TextPart,
    ToolCall,
    ToolCallDelta,
    ToolResultPart,
    ToolUsePart,
)
from .session import (
    InMemoryPendingApprovalStore,
    InMemorySessionStore,
    PendingApproval,
    PendingApprovalStore,
    SessionStore,
)
from .tools import ToolRegistry

if TYPE_CHECKING:
    from ..client import FeishuClient


@dataclass
class _Accum:
    id: str | None = None
    name: str | None = None
    arguments: str = ""


@dataclass
class StreamResult:
    r"""
    一轮流式响应归并后的完整结果。

    由 [feishu.agent.loop.accumulate_stream][] 将逐个 [feishu.agent.llm.StreamChunk][] 归并而成：
    `text` 为拼接后的全部文本，`tool_calls` 为重组完成的工具调用列表，`stop_reason` 为归一化的停止原因，
    `usage` 为可选的用量统计。

    Examples:
        >>> result = StreamResult(text="你好", tool_calls=[], stop_reason=StopReason.END_TURN)
        >>> result.text
        '你好'
        >>> result.tool_calls
        []
    """

    text: str
    tool_calls: list[ToolCall]
    stop_reason: StopReason
    usage: dict[str, int] | None = None


async def accumulate_stream(chunks: AsyncIterator[StreamChunk]) -> StreamResult:
    r"""
    将一轮流式响应的增量片段归并为一个 [feishu.agent.loop.StreamResult][]。

    文本片段按序拼接；工具调用片段按 `index` 归并，逐段累积出完整的参数 JSON 字符串，并产出有序的
    [feishu.agent.llm.ToolCall][] 列表；停止原因与用量统计取自 [feishu.agent.llm.MessageStop][]。

    Args:
        chunks: 逐个产出 [feishu.agent.llm.StreamChunk][] 的异步迭代器，通常来自
            [feishu.agent.llm.LlmBackend.stream][]。

    Returns:
        归并后的 [feishu.agent.loop.StreamResult][]。

    Examples:
        >>> import asyncio
        >>> async def chunks():
        ...     yield TextDelta(text="晴")
        ...     yield ToolCallDelta(index=0, id="c1", name="weather", arguments='{"city":"上海"}')
        ...     yield MessageStop(stop_reason=StopReason.TOOL_USE)
        >>> result = asyncio.run(accumulate_stream(chunks()))
        >>> result.text
        '晴'
        >>> result.tool_calls
        [ToolCall(id='c1', name='weather', arguments='{"city":"上海"}')]
        >>> result.stop_reason
        <StopReason.TOOL_USE: 'tool_use'>
    """
    text_parts: list[str] = []
    by_index: dict[int, _Accum] = {}
    stop_reason = StopReason.OTHER
    usage: dict[str, int] | None = None
    async for chunk in chunks:
        if isinstance(chunk, TextDelta):
            text_parts.append(chunk.text)
        elif isinstance(chunk, ToolCallDelta):
            acc = by_index.setdefault(chunk.index, _Accum())
            if acc.id is None and chunk.id is not None:
                acc.id = chunk.id
            if acc.name is None and chunk.name is not None:
                acc.name = chunk.name
            acc.arguments += chunk.arguments
        elif isinstance(chunk, MessageStop):
            stop_reason = chunk.stop_reason
            usage = chunk.usage
    tool_calls = [
        ToolCall(id=acc.id or "", name=acc.name or "", arguments=acc.arguments) for _, acc in sorted(by_index.items())
    ]
    return StreamResult(text="".join(text_parts), tool_calls=tool_calls, stop_reason=stop_reason, usage=usage)


_MENTION_RE = re.compile(r"^@_user_\d+\s*")


def session_id_for(event: Event) -> str:
    r"""
    从消息事件推导会话标识，用于隔离不同会话的对话历史。

    优先使用 `chat_id`；当消息属于话题（thread）回复时，附加 `root_id` 以将同一话题归为独立会话；
    若事件中没有 `chat_id`，则回退为 `message_id`。

    Args:
        event: 飞书消息事件，须具备 `.body` 属性。

    Returns:
        会话标识字符串。

    飞书文档:
        [接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)

    Examples:
        >>> from types import SimpleNamespace
        >>> ev = SimpleNamespace(body={"message": {"chat_id": "oc_1", "message_id": "om_1"}})
        >>> session_id_for(ev)
        'oc_1'
        >>> thread = SimpleNamespace(body={"message": {"chat_id": "oc_1", "root_id": "om_root"}})
        >>> session_id_for(thread)
        'oc_1:om_root'
        >>> dm = SimpleNamespace(body={"message": {"message_id": "om_9"}})
        >>> session_id_for(dm)
        'om_9'
    """
    message = event.body.get("message") or {}
    chat_id = message.get("chat_id")
    root_id = message.get("root_id")
    if chat_id:
        return f"{chat_id}:{root_id}" if root_id else chat_id
    return message.get("message_id") or ""


def user_message_from_event(event: Event) -> Message:
    r"""
    将飞书消息事件转换为一条用户角色的 [feishu.agent.llm.Message][]。

    文本提取委托给 [feishu.im.inbound.message_text][]，因此除纯文本外还支持富文本（`post`）消息，
    并会依据消息的 `mentions` 数组将 `@_user_N` 提及占位符解析为 `@<姓名>`；未被解析的开头占位符
    （例如事件未携带 `mentions` 时）会被去除。当无法解析出任何文本时（如图片等非文本消息），退回使用
    原始 `content` 作为文本。

    Args:
        event: 飞书消息事件，须具备 `.body` 属性。

    Returns:
        角色为 `user` 的 [feishu.agent.llm.Message][]。

    Raises:
        ValueError: 事件体中不存在 `message` 对象时抛出。

    飞书文档:
        [接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)

    Examples:
        >>> import json
        >>> from types import SimpleNamespace
        >>> body = {"message": {"message_type": "text", "content": json.dumps({"text": "@_user_1 你好"})}}
        >>> ev = SimpleNamespace(body=body)
        >>> msg = user_message_from_event(ev)
        >>> msg.role
        'user'
        >>> msg.content[0].text
        '你好'
    """
    from ..im.inbound import message_text

    message = event.body.get("message")
    if not message:
        raise ValueError("event body has no 'message' object")
    try:
        text = message_text(message)
    except (ValueError, TypeError):
        text = ""
    if text:
        text = _MENTION_RE.sub("", text).strip()
    else:
        text = message.get("content") or ""
    return Message(role="user", content=[TextPart(text=text)])


def _action_value(event: Event) -> dict[str, Any]:
    try:
        from ..cards.callback import parse_action

        return parse_action(event).value or {}
    except (TypeError, AttributeError, KeyError):
        action = event.body.get("action") or {}
        return action.get("value") or {}


class Agent:
    r"""
    智能体主循环：驱动大模型与工具协作，自动回复飞书消息。

    每收到一条消息，便载入会话历史、调用 [feishu.agent.llm.LlmBackend][] 流式生成响应，并由
    [feishu.agent.loop.accumulate_stream][] 归并结果。若模型请求调用工具，则经
    [feishu.agent.tools.ToolRegistry][] 分发执行，并将结果回传后继续下一轮，直至产出最终文本或触及
    `max_iterations` 上限。需要审批的工具会先发送审批卡片并挂起本轮，待用户在卡片上批准或拒绝后由
    [feishu.agent.loop.Agent.handle_card_action][] 恢复。

    经 [feishu.agent.dispatch.register_agent][] 注册到事件分发器后，即可自动处理消息与卡片回调事件。

    Args:
        backend: 大模型后端，须实现 [feishu.agent.llm.LlmBackend][]。
        registry: 工具注册表 [feishu.agent.tools.ToolRegistry][]。
        store: 会话历史存储。默认使用 [feishu.agent.session.InMemorySessionStore][]。
        client: 飞书客户端，用于回复消息与发送卡片；为 `None` 时跳过发送。
        approvals: 挂起审批存储。默认使用 [feishu.agent.session.InMemoryPendingApprovalStore][]。
        max_iterations: 单轮对话中模型与工具往返的最大次数。默认为 `8`。
        system: 系统提示词。
        stream: 是否以流式卡片回复。为 `True` 时经 `client.stream_card` 输出，否则调用 `client.im.reply`。
        **backend_kwargs: 透传给 [feishu.agent.llm.LlmBackend.stream][] 的额外参数。

    Raises:
        ValueError: `max_iterations` 小于 `1` 时抛出。

    飞书文档:
        [接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)

        [卡片回传交互](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-callback-communication)

    Examples:
        >>> from feishu.agent import Agent, ToolRegistry  # doctest:+SKIP
        >>> from feishu.agent.adapters.anthropic import AnthropicBackend  # doctest:+SKIP
        >>> agent = Agent(  # doctest:+SKIP
        ...     backend=AnthropicBackend(model="claude-sonnet-4-5"),
        ...     registry=ToolRegistry(),
        ...     client=client,
        ...     system="你是一个乐于助人的助手。",
        ... )
        >>> register_agent(dispatcher, agent)  # doctest:+SKIP
    """

    def __init__(
        self,
        *,
        backend: LlmBackend,
        registry: ToolRegistry,
        store: SessionStore | None = None,
        client: FeishuClient | None = None,
        approvals: PendingApprovalStore | None = None,
        max_iterations: int = 8,
        system: str | None = None,
        stream: bool = False,
        **backend_kwargs: Any,
    ) -> None:
        if max_iterations < 1:
            raise ValueError(f"max_iterations must be >= 1, got {max_iterations}")
        self.backend = backend
        self.registry = registry
        self.store: SessionStore = store or InMemorySessionStore()
        self.client = client
        self.approvals: PendingApprovalStore = approvals or InMemoryPendingApprovalStore()
        self.max_iterations = max_iterations
        self.system = system
        self.stream = stream
        self.backend_kwargs = backend_kwargs

    async def run(self, event: Event) -> None:
        r"""
        处理一条飞书消息事件：载入历史、追加用户消息并驱动主循环。

        通常无需直接调用，而是经 [feishu.agent.dispatch.register_agent][] 注册为消息事件的处理函数。

        Args:
            event: 飞书消息事件，须具备 `.body` 属性。

        飞书文档:
            [接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)

        Examples:
            >>> await agent.run(event)  # doctest:+SKIP
        """
        session_id = session_id_for(event)
        history = await self.store.get(session_id)
        history.append(user_message_from_event(event))
        await self.store.set(session_id, history)
        await self._loop(event, session_id, history)

    async def _loop(self, event: Event, session_id: str, history: list[Message]) -> None:
        result = None
        for _ in range(self.max_iterations):
            result = await accumulate_stream(
                self.backend.stream(
                    messages=history,
                    tools=self.registry.specs(),
                    system=self.system,
                    **self.backend_kwargs,
                )
            )
            if result.tool_calls and result.stop_reason == StopReason.TOOL_USE:
                assistant = self._assistant_tool_message(result)
                history.append(assistant)
                await self.store.append(session_id, assistant)
                suspended = await self._dispatch_tool_calls(event, session_id, history, result.tool_calls)
                if suspended:
                    return  # approval seam ended the turn
                continue
            assistant = Message(role="assistant", content=[TextPart(text=result.text)])
            history.append(assistant)
            await self.store.append(session_id, assistant)
            await self._finalize(event, result.text)
            return
        # Loop exhausted max_iterations without a final text turn — send a fallback reply.
        logging.getLogger("feishu").warning(
            "Agent loop reached max_iterations=%s without completing the request; sending fallback reply.",
            self.max_iterations,
        )
        fallback = (
            result.text
            if result and result.text
            else "[Reached the maximum number of steps without completing the request.]"
        )
        await self._finalize(event, fallback)

    def _assistant_tool_message(self, result: StreamResult) -> Message:
        content: list = []
        if result.text:
            content.append(TextPart(text=result.text))
        for call in result.tool_calls:
            content.append(ToolUsePart(id=call.id, name=call.name, arguments=_loads(call.arguments)))
        return Message(role="assistant", content=content)

    async def _dispatch_tool_calls(
        self, event: Event, session_id: str, history: list[Message], tool_calls: list[ToolCall]
    ) -> bool:
        for call in tool_calls:
            tool = self.registry.get(call.name)
            if tool.requires_approval:
                await self._request_approval(event, session_id, call)
                return True  # suspend the turn
            result = await self.registry.dispatch(call.name, _loads(call.arguments))
            tool_msg = Message(role="tool", content=[ToolResultPart(tool_call_id=call.id, content=_stringify(result))])
            history.append(tool_msg)
            await self.store.append(session_id, tool_msg)
        return False

    async def _request_approval(self, event: Event, session_id: str, call: ToolCall) -> None:
        approval_id = uuid4().hex
        await self.approvals.put(
            PendingApproval(
                approval_id=approval_id,
                session_id=session_id,
                tool_call_id=call.id,
                tool_name=call.name,
                arguments=_loads(call.arguments),
            )
        )
        message = event.body.get("message") or {}
        chat_id = message.get("chat_id")
        card = self._approval_card(call.name, _loads(call.arguments), approval_id)
        if self.client is not None and chat_id:
            await self.client.im.send(chat_id, card, msg_type="interactive", receive_id_type="chat_id")

    async def handle_card_action(self, event: Event) -> dict[str, Any]:
        r"""
        处理审批卡片的回传交互，恢复或终止此前挂起的对话。

        从卡片回传值中读取 `__approval__` 与 `decision`。决策无效时不消费挂起审批，用户可重试；批准则执行
        对应工具并恢复主循环，拒绝则向模型回传一条错误工具结果再恢复。无论恢复过程是否抛错，都会同步返回
        包含 `toast` 与更新后 `card` 的飞书响应。

        通常无需直接调用，而是经 [feishu.agent.dispatch.register_agent][] 注册为卡片回调事件的处理函数。

        Args:
            event: 飞书卡片回调事件，须具备 `.body` 属性。

        Returns:
            供飞书更新卡片的同步响应字典，含 `toast`（及在处理审批时的更新后 `card`）。

        飞书文档:
            [卡片回传交互](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-callback-communication)

        Examples:
            >>> await agent.handle_card_action(event)  # doctest:+SKIP
            {'toast': {'type': 'success', 'content': 'Approved'}, 'card': {...}}
        """
        value = _action_value(event)
        approval_id = value.get("__approval__")
        if not approval_id:
            return {"toast": {"type": "info", "content": "no pending approval"}}
        # Validate the decision BEFORE consuming the approval from the store.
        # A bogus/unrecognised decision must not destroy the PendingApproval so
        # the user can retry with a valid decision.
        decision = value.get("decision")
        if decision not in ("approve", "reject"):
            return {"toast": {"type": "info", "content": "invalid decision"}}
        approval = await self.approvals.pop(approval_id)
        if approval is None:
            return {"toast": {"type": "info", "content": "no pending approval"}}
        history = await self.store.get(approval.session_id)
        decided_card = self._decided_card(approval.tool_name, decision)
        toast_content = "Approved" if decision == "approve" else "Rejected"
        toast_type = "success" if decision == "approve" else "info"
        try:
            if decision == "approve":
                result = await self.registry.dispatch(approval.tool_name, approval.arguments)
                tool_msg = Message(
                    role="tool",
                    content=[ToolResultPart(tool_call_id=approval.tool_call_id, content=_stringify(result))],
                )
            else:
                tool_msg = Message(
                    role="tool",
                    content=[
                        ToolResultPart(
                            tool_call_id=approval.tool_call_id, content="User rejected this action.", is_error=True
                        )
                    ],
                )
            history.append(tool_msg)
            await self.store.append(approval.session_id, tool_msg)
            await self._loop(event, approval.session_id, history)
        except Exception:
            logging.getLogger("feishu").exception(
                "handle_card_action: error resuming agent after %s of %s (approval=%s)",
                decision,
                approval.tool_name,
                approval_id,
            )
        return {
            "toast": {"type": toast_type, "content": toast_content},
            "card": decided_card,
        }

    def _approval_card(self, tool_name: str, arguments: dict[str, Any], approval_id: str) -> dict[str, Any]:
        from ..cards.builder import Card

        return (
            Card()
            .header(f"Approve {tool_name}?", template="orange")
            .markdown(f"The agent wants to run **{tool_name}** with:\n```json\n{json.dumps(arguments, indent=2)}\n```")
            .button("Approve", value={"__approval__": approval_id, "decision": "approve"}, type="primary")
            .button("Reject", value={"__approval__": approval_id, "decision": "reject"}, type="danger")
            .to_dict()
        )

    def _decided_card(self, tool_name: str, decision: str) -> dict[str, Any]:
        from ..cards.builder import Card

        verb = "approved" if decision == "approve" else "rejected"
        return (
            Card()
            .header(f"{tool_name} {verb}", template="green" if decision == "approve" else "grey")
            .markdown(f"Action **{tool_name}** was {verb}.")
            .to_dict()
        )

    async def _finalize(self, event: Event, text: str) -> None:
        message = event.body.get("message") or {}
        message_id = message.get("message_id")
        if self.stream and self.client is not None:
            await self._finalize_stream(event, text)
            return
        if self.client is not None and message_id:
            await self.client.im.reply(message_id, text, msg_type="text")

    async def _finalize_stream(self, event: Event, text: str) -> None:
        message = event.body.get("message") or {}
        message_id = message.get("message_id")
        # Mirror the non-stream _finalize: reply in-thread to the inbound message; skip if absent.
        if not message_id:
            return

        async def _one_token() -> AsyncIterator[str]:
            yield text

        await self.client.stream_card(_one_token(), reply_to_message_id=message_id)  # type: ignore[union-attr]


def _loads(arguments: str) -> dict[str, Any]:
    if not arguments:
        return {}
    try:
        return json.loads(arguments)
    except (ValueError, TypeError):
        return {}


def _stringify(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)
