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

import asyncio
import inspect
import logging
import re
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal
from uuid import uuid4
from weakref import WeakValueDictionary

from ..events.conversations import is_group_message, session_id_for_message
from ..events.envelope import Event
from ..events.jobs import (
    EventSuperseded,
    current_event_effect,
    current_event_job,
    event_checkpoint,
    mark_current_event_side_effect_started,
)
from . import approval as approval_flow
from . import oauth as oauth_flow
from . import summarization as summarization_flow
from ._callbacks import accepts_positional_arguments as _accepts_positional_arguments
from ._flow import AUTH_CARD_SENT_NOTE as _AUTH_CARD_SENT_NOTE
from ._flow import AWAITING_APPROVAL_NOTE as _AWAITING_APPROVAL_NOTE
from ._flow import AWAITING_AUTHORIZATION_NOTE as _AWAITING_AUTHORIZATION_NOTE
from ._flow import INTERRUPTED_TOOL_NOTE as _INTERRUPTED_TOOL_NOTE
from ._flow import replace_tool_result as _replace_tool_result
from ._flow import suspension_progress_note as _suspension_progress_note
from ._flow import tool_calls_after as _tool_calls_after
from .approval import ApprovalEngine, ApprovalOutcome, DefaultApprovalEngine
from .context import ToolContext, current_tool_context, use_tool_context
from .llm import (
    ContentPart,
    LlmBackend,
    Message,
    MessageStop,
    ReasoningDelta,
    ReasoningPart,
    StopReason,
    StreamChunk,
    TextDelta,
    TextPart,
    ToolCall,
    ToolCallDelta,
    ToolResultPart,
    ToolUsePart,
    parse_tool_arguments,
)
from .profiles import namespaced_session_id
from .progress import ProgressSnapshot, _message_id_from_response, _ProgressCard
from .replies import ScheduledTurn, turn_message
from .result import ToolOutcome, ToolResult, coerce_tool_result
from .session import (
    InMemoryPendingApprovalStore,
    InMemoryPendingAuthorizationStore,
    InMemorySessionStore,
    PendingApproval,
    PendingApprovalStore,
    PendingAuthorization,
    PendingAuthorizationStore,
    SessionStore,
)
from .shared_files import shared_files_note
from .tools import Tool, ToolRegistry

if TYPE_CHECKING:
    from ..client import FeishuClient


@dataclass
class _Accum:
    id: str | None = None
    name: str | None = None
    arguments: str = ""


@dataclass
class _ActiveTurn:
    task: asyncio.Task[Any]
    progress: _ProgressCard


@dataclass
class StreamResult:
    r"""
    一轮流式响应归并后的完整结果。

    由 [feishu.agent.loop.accumulate_stream][] 将逐个 [feishu.agent.llm.StreamChunk][] 归并而成：
    `text` 为拼接后的全部文本，`tool_calls` 为重组完成的工具调用列表，`stop_reason` 为归一化的停止原因，
    `usage` 为可选的用量统计，`reasoning` 为归并后的（可选）推理/思考文本。

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
    reasoning: str = ""


class _StreamAccumulator:
    """Collect model state while a single consumer forwards text to the reply."""

    def __init__(self) -> None:
        self.result = StreamResult(text="", tool_calls=[], stop_reason=StopReason.OTHER)
        self._calls: dict[int, _Accum] = {}

    def add(self, chunk: StreamChunk) -> None:
        if isinstance(chunk, TextDelta):
            self.result.text += chunk.text
        elif isinstance(chunk, ReasoningDelta):
            self.result.reasoning += chunk.text
        elif isinstance(chunk, ToolCallDelta):
            acc = self._calls.setdefault(chunk.index, _Accum())
            if acc.id is None and chunk.id is not None:
                acc.id = chunk.id
            if acc.name is None and chunk.name is not None:
                acc.name = chunk.name
            acc.arguments += chunk.arguments
        elif isinstance(chunk, MessageStop):
            self.result.stop_reason = chunk.stop_reason
            self.result.usage = chunk.usage

    def finish(self) -> StreamResult:
        self.result.tool_calls = [
            ToolCall(id=call.id or "", name=call.name, arguments=call.arguments)
            for _, call in sorted(self._calls.items())
            if call.name
        ]
        return self.result


async def accumulate_stream(
    chunks: AsyncIterator[StreamChunk],
    *,
    on_reasoning: Callable[[str, str], Awaitable[Any] | Any] | None = None,
) -> StreamResult:
    r"""
    将一轮流式响应的增量片段归并为一个 [feishu.agent.loop.StreamResult][]。

    文本片段按序拼接；工具调用片段按 `index` 归并，逐段累积出完整的参数 JSON 字符串，并产出有序的
    [feishu.agent.llm.ToolCall][] 列表；停止原因与用量统计取自 [feishu.agent.llm.MessageStop][]；推理片段
    （[feishu.agent.llm.ReasoningDelta][]）按序拼接为 `reasoning`。

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
    accumulator = _StreamAccumulator()
    async for chunk in chunks:
        accumulator.add(chunk)
        if isinstance(chunk, ReasoningDelta) and on_reasoning is not None:
            update = on_reasoning(accumulator.result.reasoning, accumulator.result.text)
            if inspect.isawaitable(update):
                await update
    return accumulator.finish()


_MENTION_RE = re.compile(r"^@_user_\d+\s*")


def session_id_for(event: Event) -> str:
    r"""
    从消息事件推导会话标识，用于隔离不同会话的对话历史。

    私聊使用 `chat_id`；群聊以 `chat_id` 和根消息标识隔离独立问题，回复沿用 `root_id`。
    缺少 `chat_type` 的旧事件保留原来的 `chat_id` / `root_id` 路由。

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
    return session_id_for_message(event)


def _sender_type_for(event: Event) -> str:
    sender = event.body.get("sender") or {}
    return str(sender.get("sender_type") or "").strip().lower()


def user_message_from_event(event: Event) -> Message:
    r"""
    将飞书消息事件转换为一条用户角色的 [feishu.agent.llm.Message][]。

    文本提取委托给 [feishu.im.inbound.message_text][]，因此除纯文本外还支持富文本（`post`）消息，
    并会依据消息的 `mentions` 数组将 `@_user_N` 提及占位符解析为 `@<姓名>`；未被解析的开头占位符
    （例如事件未携带 `mentions` 时）会被去除。当无法解析出任何文本时：纯文本消息退回使用原始 `content`，
    其他类型（如图片、文件）则返回形如 `[<message_type> message]` 的中性占位文本。

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
        message_type = str(message.get("message_type") or message.get("msg_type") or "non-text")
        if message_type == "text":
            text = str(message.get("content") or "")
        else:
            text = f"[{message_type} message]"
    return Message(role="user", content=[TextPart(text=text)])


async def _dynamic_text(value: str | Callable[..., Any] | None, event: Event, timezone: str | None) -> str | None:
    if callable(value):
        if _accepts_positional_arguments(value, 2):
            value = value(event, timezone)
        elif _accepts_positional_arguments(value, 1):
            value = value(event)
        else:
            value = value()
        if inspect.isawaitable(value):
            value = await value
    if value is None or isinstance(value, str):
        return value
    return str(value)


def _messages_with_turn_context(history: list[Message], turn_context: str | None) -> list[Message]:
    context = (turn_context or "").strip()
    if not context:
        return history
    user_index = next((index for index in range(len(history) - 1, -1, -1) if history[index].role == "user"), None)
    if user_index is None:
        return history
    current_user = history[user_index]
    return [
        *history[:user_index],
        Message(
            role=current_user.role,
            content=[*current_user.content, TextPart(text=f"\n\n{context}")],
        ),
        *history[user_index + 1 :],
    ]


def _default_now() -> float:
    return time.time()


async def _cooperative_chunks(chunks: AsyncIterator[StreamChunk]) -> AsyncGenerator[StreamChunk, None]:
    """Stop waiting for model output without cancelling an external tool or card write."""
    job = current_event_job()
    cancellation = job.cancellation if job is not None and job.interruptible else None
    if cancellation is None:
        async for chunk in chunks:
            yield chunk
        return
    iterator = aiter(chunks)
    while not cancellation.is_set():
        next_chunk = asyncio.ensure_future(anext(iterator))
        interrupted = asyncio.create_task(cancellation.wait())
        try:
            done, _ = await asyncio.wait((next_chunk, interrupted), return_when=asyncio.FIRST_COMPLETED)
            if interrupted in done:
                next_chunk.cancel()
                await asyncio.gather(next_chunk, return_exceptions=True)
                return
            try:
                yield next_chunk.result()
            except StopAsyncIteration:
                return
        finally:
            interrupted.cancel()
            await asyncio.gather(interrupted, return_exceptions=True)
            if not next_chunk.done():
                next_chunk.cancel()
                await asyncio.gather(next_chunk, return_exceptions=True)


class AgentEngine:
    r"""
    智能体底层主循环：驱动大模型与工具协作，自动回复飞书消息。

    每收到一条消息，便载入会话历史、调用 [feishu.agent.llm.LlmBackend][] 流式生成响应，并由
    [feishu.agent.loop.accumulate_stream][] 归并结果。若模型请求调用工具，则经
    [feishu.agent.tools.ToolRegistry][] 分发执行，并将结果回传后继续下一轮，直至产出最终文本或触及
    `max_iterations` 上限。需要审批的工具会先发送审批卡片并挂起本轮，待用户在卡片上批准或拒绝后由
    [feishu.agent.loop.AgentEngine.handle_card_action][] 恢复。

    经 [feishu.agent.registration.register_agent][] 注册到事件分发器后，即可自动处理消息与卡片回调事件。

    Args:
        backend: 大模型后端，须实现 [feishu.agent.llm.LlmBackend][]。
        registry: 工具注册表 [feishu.agent.tools.ToolRegistry][]。
        store: 会话历史存储。默认使用 [feishu.agent.session.InMemorySessionStore][]。
        client: 飞书客户端，用于回复消息与发送卡片；为 `None` 时跳过发送。
        approvals: 挂起审批存储。默认使用 [feishu.agent.session.InMemoryPendingApprovalStore][]。
        authorizations: 挂起授权存储。默认使用 [feishu.agent.session.InMemoryPendingAuthorizationStore][]。
        approval_engine: 人在环审批引擎。默认使用 [feishu.agent.approval.DefaultApprovalEngine][]（基于
            `approvals`），提供负载防篡改校验、幂等重放、并发认领与审计。
        approval_card_builder: 审批卡片构造器，签名为 `(PendingApproval) -> dict`。默认内置卡片，按钮回传值
            携带 `payload_sha256` 供回调时防篡改校验。
        decided_card_builder: 决策结果卡片构造器，签名为 `(PendingApproval, decision, ApprovalOutcome) -> dict`。
        auth_card_builder: 授权卡片构造器，签名为 `(authorize_url) -> dict`；缺少用户授权时以交互卡片（按钮）
            引导授权，未注入时回退为在工具结果中附上授权链接。
        progress_card_builder: 进度卡片构造器，签名为 `(tool_names, done, result_text) -> dict`；循环开始时
            展示「处理中」，调用工具时原地更新步骤，完成后替换为最终答复，未注入时不展示进度。
        progress_summarizer: 可选的进度文案生成器，签名为 `(ProgressSnapshot) -> str | None`（可异步）。
            SDK 会把近期 reasoning 仅在内存中传入，返回值用于原地更新进度卡片。
        user_tokens: 用户态 token 提供方（[feishu.auth][]），供工具以用户身份执行；为 `None` 时
            [feishu.agent.context.ToolContext.as_user][] 返回 `None`。
        authorize_url_builder: 产品注入的授权 URL 构造器，签名
            `(user, scopes, pending_authorization=None) -> str | None`。SDK 工具缺少用户授权时会传入
            `PendingAuthorization`，产品可把其 `authorization_id` 签入 OAuth state，以便 callback 自动恢复。
        shared_files: 用户分享文件的解析器 [feishu.agent.shared_files.SharedFileResolver][]，是 `file_id` 句柄到
            字节的唯一入口；为 `None` 时相关工具不可用。
        shared_files_store: 入站文件句柄存储 [feishu.agent.shared_files.SharedFileStore][]，用于捕获用户分享的
            文件（仅元数据，不落字节）；为 `None` 时不捕获。
        shared_file_ttl_seconds: 文件句柄的存活时长（秒）。默认为 7 天。
        shared_files_private_only: 是否仅在单聊（p2p）中捕获分享文件。默认为 `True`。
        payment_accounts: 收款账户解析器 [feishu.agent.payment_accounts.PaymentAccountResolver][]，把账户句柄
            还原为可提交的账户值（严格按请求用户隔离）；为 `None` 时相关工具不可用。
        clear_command: 判定文本是否为「清空会话」命令的谓词 `(text) -> bool`；命中时彻底删除该会话历史并直接回复
            `clear_reply`，不调用模型。为 `None` 时不启用。
        clear_reply: 清空会话后的回执文案。
        compact_command: 判定文本是否为「立即压缩上下文」命令的谓词 `(text) -> bool`；命中时立刻摘要并直接回复，
            不调用模型。为 `None` 时不启用。
        compact_reply: 压缩回执构造器，签名 `(before_count, after_count) -> str`；为 `None` 时用中性默认文案。
        summarizer: 自定义摘要器 `(messages) -> str`（协程）；为 `None` 时回退为用 `backend` 生成摘要。
        summarize_threshold_tokens: 历史超过该估算 token 数时自动压缩（摘要旧轮次、保留最近若干轮），以维持可被
            前缀缓存命中的稳定 prefix。为 `0` 时关闭自动压缩。
        summarize_keep_recent: 自动 / 手动压缩时原样保留的最近消息条数。默认为 `12`。
        summary_instruction: 默认摘要器使用的指令文案。
        summary_prefix: 摘要消息的前缀标记。
        max_iterations: 单轮对话中模型与工具往返的最大次数。默认为 `8`。
        system: 系统提示词；也可以传入 callable，每轮调用前动态生成。callable 可接收 `(event, timezone)`、
            `event`，或不接收参数。
        turn_context: 每轮附加给模型的动态上下文；也可以传入 callable，签名同 `system`。SDK 会把它临时追加到
            当前 user message 末尾，不写入会话历史，避免动态时间等信息破坏 system/历史前缀缓存。
        idle_session_timeout_seconds: 会话空闲超过该秒数后自动清空普通历史；`0` 或负数表示关闭。
        timezone: 本轮默认时区；也可以传入 callable，每轮按事件动态解析。卡片回调中若包含用户时区，会优先使用。
        interrupted_progress_text: 同一会话的新消息打断旧轮次时，旧进度卡收尾展示的文案。
        stream: 未使用进度卡片时是否以流式卡片回复。为 `True` 时经 `client.stream_card` 输出，否则调用
            `client.im.reply`。
        **backend_kwargs: 透传给 [feishu.agent.llm.LlmBackend.stream][] 的额外参数。

    Raises:
        ValueError: `max_iterations` 小于 `1` 时抛出。

    飞书文档:
        [接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)

        [卡片回传交互](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-callback-communication)

    Examples:
        >>> from feishu.agent.loop import AgentEngine  # doctest:+SKIP
        >>> from feishu.agent import ToolRegistry  # doctest:+SKIP
        >>> from feishu.agent.adapters.anthropic import AnthropicBackend  # doctest:+SKIP
        >>> agent = AgentEngine(  # doctest:+SKIP
        ...     backend=AnthropicBackend(model="claude-sonnet-4-5"),
        ...     registry=ToolRegistry(),
        ...     client=client,
        ...     system="你是一个乐于助人的助手。",
        ... )
        >>> register_agent(dispatcher, agent)  # doctest:+SKIP
    """

    @property
    def registry(self) -> Any:
        from .scheduled_policy import current_scheduled_registry

        return current_scheduled_registry(self._registry)

    @registry.setter
    def registry(self, value: Any) -> None:
        self._registry = value

    @property
    def store(self) -> Any:
        from .event_session import current_event_session
        from .scheduled_session import current_fenced_session

        event_store = current_event_session(self._store)
        return event_store if event_store is not self._store else current_fenced_session(self._store)

    @store.setter
    def store(self, value: Any) -> None:
        self._store = value

    def __init__(
        self,
        *,
        backend: LlmBackend,
        registry: ToolRegistry,
        store: SessionStore | None = None,
        client: FeishuClient | None = None,
        approvals: PendingApprovalStore | None = None,
        authorizations: PendingAuthorizationStore | None = None,
        approval_engine: ApprovalEngine | None = None,
        approval_card_builder: Callable[[PendingApproval], dict[str, Any]] | None = None,
        decided_card_builder: Callable[[PendingApproval, str, ApprovalOutcome], dict[str, Any]] | None = None,
        auth_card_builder: Callable[[str], dict[str, Any]] | None = None,
        progress_card_builder: Callable[[list[str], bool, str], dict[str, Any]] | None = None,
        progress_summarizer: Callable[[ProgressSnapshot], Any] | None = None,
        progress_summary_delay_seconds: float = 1.0,
        progress_summary_interval_seconds: float = 2.0,
        progress_reasoning_max_chars: int = 4000,
        user_tokens: Any = None,
        authorize_url_builder: Any = None,
        shared_files: Any = None,
        shared_files_store: Any = None,
        shared_file_ttl_seconds: int = 7 * 24 * 3600,
        shared_files_private_only: bool = True,
        payment_accounts: Any = None,
        memory_store: Any = None,
        memory_namespace: str | None = None,
        clear_command: Callable[[str], bool] | None = None,
        clear_reply: str = "会话历史已清空。",
        compact_command: Callable[[str], bool] | None = None,
        compact_reply: Callable[[int, int], str] | None = None,
        summarizer: Callable[[list[Message]], Any] | None = None,
        summarize_threshold_tokens: int = 0,
        summarize_keep_recent: int = 12,
        summary_instruction: str = (
            "Summarize the earlier conversation below so it can stand in for the full history: preserve "
            "facts, decisions, open tasks, identifiers, and user preferences; drop pleasantries. Be concise."
        ),
        summary_prefix: str = "[Summary of earlier conversation]",
        max_iterations: int = 8,
        system: str | Callable[..., Any] | None = None,
        turn_context: str | Callable[..., Any] | None = None,
        idle_session_timeout_seconds: float = 0.0,
        session_namespace: str | None = None,
        now: Callable[[], float] | None = None,
        timezone: str | Callable[..., Any] | None = None,
        interrupted_progress_text: str = "已被更新的消息打断。",
        stream: bool = False,
        scheduled_store: Any = None,
        trusted_tenant_key: str | None = None,
        **backend_kwargs: Any,
    ) -> None:
        if max_iterations < 1:
            raise ValueError(f"max_iterations must be >= 1, got {max_iterations}")
        if session_namespace is not None and "::" in session_namespace:
            raise ValueError("session namespace cannot contain '::'")
        self.backend = backend
        self.registry = registry
        self.store: SessionStore = store or InMemorySessionStore()
        self.client = client
        self.approvals: PendingApprovalStore = approvals or InMemoryPendingApprovalStore()
        self.authorizations: PendingAuthorizationStore = authorizations or InMemoryPendingAuthorizationStore()
        self.approval_engine: ApprovalEngine = approval_engine or DefaultApprovalEngine(approvals=self.approvals)
        self._product_approval_card_builder = approval_card_builder or approval_flow.default_approval_card
        self._approval_card_builder = self._build_approval_card
        self._decided_card_builder = decided_card_builder or approval_flow.default_decided_card
        self._auth_card_builder = auth_card_builder
        self._progress_card_builder = progress_card_builder
        self._progress_summarizer = progress_summarizer
        self._progress_summary_delay_seconds = max(0.0, progress_summary_delay_seconds)
        self._progress_summary_interval_seconds = max(0.0, progress_summary_interval_seconds)
        self._progress_reasoning_max_chars = max(0, progress_reasoning_max_chars)
        self.user_tokens = user_tokens
        self.authorize_url_builder = authorize_url_builder
        self.shared_files = shared_files  # a SharedFileResolver (file_id -> bytes chokepoint), or None
        self._shared_files_store = shared_files_store  # a SharedFileStore for inbound capture, or None
        self._shared_file_ttl_seconds = shared_file_ttl_seconds
        self._shared_files_private_only = shared_files_private_only  # only capture files in 1:1 chats by default
        self.payment_accounts = payment_accounts  # a PaymentAccountResolver (account handle -> value), or None
        self.memory_store = memory_store
        self.memory_namespace = memory_namespace
        self._clear_command = clear_command  # predicate on the user's text: True -> reset this session
        self._clear_reply = clear_reply
        self._compact_command = compact_command  # predicate on the user's text: True -> compact this session now
        self._compact_reply = compact_reply  # (before_count, after_count) -> reply text
        self._summarizer = summarizer  # custom (messages) -> summary text; None uses the backend default
        self._summarize_threshold_tokens = summarize_threshold_tokens  # 0 disables summarization
        self._summarize_keep_recent = summarize_keep_recent
        self._summary_instruction = summary_instruction
        self._summary_prefix = summary_prefix
        self.max_iterations = max_iterations
        self.system = system
        self.turn_context = turn_context
        self.idle_session_timeout_seconds = max(0.0, float(idle_session_timeout_seconds or 0.0))
        self._session_namespace = session_namespace or ""
        self._now = now or _default_now
        self.timezone = timezone
        self._interrupted_progress_text = interrupted_progress_text
        self.stream = stream
        self.scheduled_store = scheduled_store
        self.trusted_tenant_key = trusted_tenant_key
        self.backend_kwargs = backend_kwargs
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._active_turns: dict[str, _ActiveTurn] = {}
        self._turn_tasks: set[asyncio.Task[Any]] = set()
        self._superseded_turns: set[asyncio.Task[Any]] = set()
        self._accepting_events = True
        # Per-session lock: serialize each session's history read-modify-write (a new message and a background
        # approval-resume for the same chat must not interleave and clobber each other's turns).
        # Running and queued turns retain their lock; completed group roots should not
        # leave one lock behind for every message the long-lived engine has seen.
        self._session_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        r"""返回该会话的串行化锁（不存在则创建）。在单事件循环中 get/setdefault 之间无 await，故创建是安全的。"""
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        return lock

    @property
    def accepting_events(self) -> bool:
        """Whether new message, card, and authorization work may be accepted."""
        return self._accepting_events

    def stop_accepting_events(self) -> None:
        """Prevent new work from starting while existing turns drain."""
        self._accepting_events = False

    def track_background_task(self, task: asyncio.Task[Any]) -> None:
        """Register a task whose work uses this engine's stores or client."""
        if not self._accepting_events:
            task.cancel()
            return
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def aclose(self, *, deadline: float | None = None) -> bool:
        """Stop accepting work, wait for active work, and cancel it if the deadline expires.

        ``deadline`` is an absolute ``asyncio`` loop time so an application runtime can share
        one shutdown budget across its server, engine, and owned resources.
        """
        self.stop_accepting_events()
        current = asyncio.current_task()
        tasks = set(self._bg_tasks)
        tasks.update(self._turn_tasks)
        tasks.update(turn.task for turn in self._active_turns.values())
        tasks.update(self._superseded_turns)
        if current is not None:
            tasks.discard(current)
        tasks = {task for task in tasks if not task.done()}
        if not tasks:
            return True

        loop = asyncio.get_running_loop()
        timeout = None if deadline is None else max(0.0, deadline - loop.time())
        _done, pending = await asyncio.wait(tasks, timeout=timeout)
        if not pending:
            return True

        for task in pending:
            task.cancel()
        if deadline is None:
            await asyncio.gather(*pending, return_exceptions=True)
            return True

        remaining = max(0.0, deadline - loop.time())
        _done, pending = await asyncio.wait(pending, timeout=remaining)
        if pending:
            logging.getLogger("feishu").error(
                "agent shutdown deadline expired with %d task(s) still running", len(pending)
            )
            return False
        return True

    def _session_id_for_event(self, event: Event) -> str:
        session_id = session_id_for(event)
        return namespaced_session_id(self._session_namespace, session_id) if self._session_namespace else session_id

    @property
    def session_namespace(self) -> str:
        """The stable profile prefix applied to every persisted session ID."""
        return self._session_namespace

    def _begin_session_turn(
        self, session_id: str, progress: _ProgressCard, *, interrupt_previous: bool = False
    ) -> asyncio.Task[Any] | None:
        r"""把当前任务登记为该会话的活跃轮次，并可选择中断上一轮。"""
        task = asyncio.current_task()
        if task is None:
            return None
        self._turn_tasks.add(task)
        previous = self._active_turns.get(session_id)
        if interrupt_previous and previous is not None and previous.task is not task and not previous.task.done():
            self._superseded_turns.add(previous.task)
            previous.task.cancel()
        self._active_turns[session_id] = _ActiveTurn(task=task, progress=progress)
        return task

    def _end_session_turn(self, session_id: str, task: asyncio.Task[Any] | None) -> None:
        if task is None:
            return
        self._turn_tasks.discard(task)
        active = self._active_turns.get(session_id)
        if active is not None and active.task is task:
            self._active_turns.pop(session_id, None)
        self._superseded_turns.discard(task)

    def _turn_was_superseded(self, task: asyncio.Task[Any] | None) -> bool:
        return task is not None and task in self._superseded_turns

    async def _finalize_interrupted_progress(self, progress: _ProgressCard) -> None:
        text = self._interrupted_progress_text
        if not text:
            return
        try:
            await progress.finalize(text)
        except Exception:  # noqa: BLE001 - cancellation cleanup must not hide the newer turn
            logging.getLogger("feishu").debug("could not finalize interrupted progress card", exc_info=True)

    async def _clear_idle_session(self, session_id: str) -> None:
        if self.idle_session_timeout_seconds <= 0:
            return
        updated_at = await self.store.updated_at(session_id)
        if updated_at is None:
            return
        if self._now() - updated_at > self.idle_session_timeout_seconds:
            await self.store.clear(session_id)

    async def run(self, event: Event) -> None:
        r"""
        处理一条飞书消息事件：载入历史、追加用户消息并驱动主循环。

        通常无需直接调用，而是经 [feishu.agent.registration.register_agent][] 注册为消息事件的处理函数。

        Args:
            event: 飞书消息事件，须具备 `.body` 属性。

        飞书文档:
            [接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)

        Examples:
            >>> await agent.run(event)  # doctest:+SKIP
        """
        if not self._accepting_events:
            raise RuntimeError("agent is shutting down and is not accepting events")
        if _sender_type_for(event) == "app":
            logging.getLogger("feishu").debug("ignore app-sent message event")
            return
        session_id = self._session_id_for_event(event)
        user_msg = user_message_from_event(event)
        event_id = str(getattr(event, "event_id", "") or "")
        user_msg.source_event_id = event_id or None
        progress = _ProgressCard(self, event)
        active_task = self._begin_session_turn(session_id, progress, interrupt_previous=not is_group_message(event))
        job = current_event_job()
        session_scope: Any = nullcontext()
        if job is not None and job.lease_token:
            from .event_session import fenced_event_session_scope

            session_scope = fenced_event_session_scope(self._store, job, session_id)
        try:
            # Serialize per session: hold the session lock across the whole read-modify-write + loop so a
            # concurrent message (or a background approval-resume) for the same chat can't load stale history
            # and clobber a turn.
            with session_scope, use_tool_context(self._tool_context(event)):
                async with self._session_lock(session_id):
                    # A reset command (e.g. "/clear") truly DROPS this session's history (cache-friendly: starts a
                    # fresh prefix rather than sliding a window), then acks — without running the model.
                    if self._clear_command is not None and self._clear_command(_message_text(user_msg)):
                        await self.store.clear(session_id)
                        await self._finalize(event, self._clear_reply)
                        return
                    # A compact command summarizes the session NOW (regardless of the auto threshold) and acks — also
                    # without running the model. The command message itself is not added to the history.
                    if self._compact_command is not None and self._compact_command(_message_text(user_msg)):
                        existing = await self.store.get(session_id)
                        compacted = await self._summarize_history(session_id, existing)
                        reply = (self._compact_reply or summarization_flow.default_compact_reply)(
                            len(existing), len(compacted)
                        )
                        await self._finalize(event, reply)
                        return
                    await self._clear_idle_session(session_id)
                    history = await self.store.get(session_id)
                    await self._recover_interrupted_tool_calls(session_id, history)
                    already_recorded = bool(event_id) and any(
                        message.role == "user" and message.source_event_id == event_id for message in history
                    )
                    if not already_recorded:
                        shared = await self._register_inbound_files(event)
                        if shared:
                            # Make the model aware of the just-shared files (by opaque handle only — never bytes).
                            user_msg.content.append(TextPart(text=shared_files_note(shared)))
                        history.append(user_msg)
                        await self.store.set(session_id, history)
                    event_checkpoint()
                    await self._loop(event, session_id, history, progress=progress)
        except EventSuperseded:
            await self._finalize_interrupted_progress(progress)
            raise
        except asyncio.CancelledError:
            if self._turn_was_superseded(active_task):
                await self._finalize_interrupted_progress(progress)
                return
            raise
        finally:
            self._end_session_turn(session_id, active_task)

    async def run_scheduled(self, turn: ScheduledTurn, instruction: str) -> str:
        """Run an explicit owner-bound scheduled turn in its own session.

        The caller must authenticate the stored owner and ensure durable claim
        before invoking this method. No Feishu inbound event is manufactured.
        """
        from .scheduled_policy import scheduled_registry_scope
        from .scheduled_session import fenced_session_scope

        if not self._accepting_events:
            raise RuntimeError("agent is shutting down and is not accepting work")
        if not isinstance(turn, ScheduledTurn) or not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("a scheduled turn and instruction are required")
        if self.scheduled_store is None:
            raise ValueError("a persistent scheduled store is required")
        from ..auth import user_identity_keys

        run = self.scheduled_store.get_run(turn.run_id)
        task = self.scheduled_store.get(run.task_id) if run is not None else None
        if (
            run is None
            or task is None
            or run.status != "running"
            or run.session_id != turn.session_id
            or task.chat_id != turn.chat_id
            or task.tenant_key != turn.tenant_key
            or task.policy != turn.policy
            or task.instruction != instruction
            or set(user_identity_keys(turn.user)) != set(task.owner_keys)
        ):
            raise PermissionError("scheduled turn does not match its claimed task")
        self._assert_scheduled_lease(turn)
        progress = _ProgressCard(self, turn)
        active_task = self._begin_session_turn(turn.session_id, progress, interrupt_previous=False)
        try:
            with (
                scheduled_registry_scope(
                    self._registry, turn.policy, lease_validator=lambda: self._assert_scheduled_lease(turn)
                ),
                fenced_session_scope(self._store, self.scheduled_store, turn, clock=self._now),
                use_tool_context(self._tool_context(turn)),
            ):
                async with self._session_lock(turn.session_id):
                    if await self.store.get(turn.session_id):
                        raise ValueError("scheduled session already exists")
                    history = [Message(role="user", content=[TextPart(text=instruction)])]
                    await self.store.set(turn.session_id, history)
                    return await self._loop(turn, turn.session_id, history, progress=progress)
        finally:
            self._end_session_turn(turn.session_id, active_task)

    def _scheduled_run_for(self, turn: ScheduledTurn) -> Any:
        from ..auth import user_identity_keys

        if self.scheduled_store is None:
            raise RuntimeError("scheduled continuation has no persistent scheduled store")
        run = self.scheduled_store.get_run(turn.run_id)
        task = self.scheduled_store.get(run.task_id) if run is not None else None
        if (
            run is None
            or task is None
            or run.session_id != turn.session_id
            or task.chat_id != turn.chat_id
            or task.tenant_key != turn.tenant_key
            or task.policy != turn.policy
            or set(user_identity_keys(turn.user)) != set(task.owner_keys)
        ):
            raise PermissionError("scheduled continuation does not match its claimed task")
        self._assert_scheduled_lease(turn)
        return run

    def _assert_scheduled_lease(self, turn: Event | ScheduledTurn) -> None:
        if isinstance(turn, ScheduledTurn):
            if self.scheduled_store is None:
                raise PermissionError("scheduled store unavailable")
            self.scheduled_store.validate_lease(turn.run_id, turn.lease_token, now=self._now())

    async def _record_scheduled_wait(
        self, turn: Event | ScheduledTurn, kind: str, wait_id: str, deadline: float
    ) -> None:
        if isinstance(turn, ScheduledTurn):
            self._scheduled_run_for(turn)
            self.scheduled_store.record_wait(
                turn.run_id, kind, wait_id, deadline, expected_token=turn.lease_token, now=self._now()
            )

    async def _clear_scheduled_wait(self, turn: Event | ScheduledTurn, kind: str, wait_id: str) -> None:
        if isinstance(turn, ScheduledTurn):
            run = self._scheduled_run_for(turn)
            if run.wait_kind == kind:
                self.scheduled_store.clear_wait(turn.run_id, wait_id, expected_token=turn.lease_token, now=self._now())

    async def _settle_scheduled_resume(self, turn: Event | ScheduledTurn, status: str) -> None:
        if not isinstance(turn, ScheduledTurn):
            return
        self._scheduled_run_for(turn)
        self.scheduled_store.finish_run(
            turn.run_id, status, now=self._now(), expected_status="running", expected_token=turn.lease_token
        )

    async def _loop(
        self,
        event: Event | ScheduledTurn,
        session_id: str,
        history: list[Message],
        progress: _ProgressCard | None = None,
    ) -> str:
        progress = progress or _ProgressCard(self, event)
        result = None
        streamed = False
        active_tool_calls: list[ToolCall] | None = None
        # Compact the history at a token threshold (collapse old turns into one summary message) BEFORE the
        # model call, so a long session keeps a stable, cacheable prefix instead of paying a growing prompt.
        try:
            history = await self._maybe_summarize(session_id, history)
            event_checkpoint()
            if progress.will_start:
                await mark_current_event_side_effect_started()
            await progress.start()
            timezone = await self._timezone_for_event(event)
            for _ in range(self.max_iterations):
                event_checkpoint()
                self._assert_scheduled_lease(event)
                system = await self._system_for_event(event, timezone)
                messages = _messages_with_turn_context(history, await self._turn_context_for_event(event, timezone))
                result, streamed = await self._accumulate_stream_with_progress(
                    event,
                    self.backend.stream(
                        messages=messages,
                        tools=self.registry.specs(),
                        system=system,
                        **self.backend_kwargs,
                    ),
                    progress,
                )
                if result.usage:
                    cached = result.usage.get("cached_tokens")
                    prompt = result.usage.get("prompt_tokens")
                    if cached is not None and prompt:
                        logging.getLogger("feishu").info(
                            "llm usage: prompt=%s cached=%s (%.0f%% hit) completion=%s",
                            prompt,
                            cached,
                            100.0 * cached / prompt,
                            result.usage.get("completion_tokens"),
                        )
                if result.tool_calls and result.stop_reason == StopReason.TOOL_USE:
                    assistant = self._assistant_tool_message(result)
                    current_tool_context().continuation_id = assistant.continuation_id
                    await mark_current_event_side_effect_started()
                    history.append(assistant)
                    await self.store.append(session_id, assistant)
                    active_tool_calls = result.tool_calls
                    suspension = await self._dispatch_tool_calls(
                        event, session_id, history, result.tool_calls, progress
                    )
                    active_tool_calls = None
                    if suspension:
                        await progress.finalize(_suspension_progress_note(suspension))
                        return "awaiting_authorization" if suspension == "authorization" else "awaiting_confirmation"
                    continue
                assistant = Message(role="assistant", content=[TextPart(text=result.text)])
                await mark_current_event_side_effect_started()
                history.append(assistant)
                await self.store.append(session_id, assistant)
                # Replace the progress card in place with the answer; only send a separate reply if there was none.
                if not await progress.finalize(result.text) and not streamed:
                    await self._finalize(event, result.text)
                return "succeeded"
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
            if not await progress.finalize(fallback) and not streamed:
                await self._finalize(event, fallback)
            return "failed"
        except (asyncio.CancelledError, EventSuperseded):
            if active_tool_calls:
                await self._mark_interrupted_tool_results(session_id, history, active_tool_calls)
            raise

    async def _accumulate_stream_with_progress(
        self, event: Any, chunks: AsyncIterator[StreamChunk], progress: _ProgressCard
    ) -> tuple[StreamResult, bool]:
        r"""消费一轮模型流，同时推送正文；推理内容只交给进度摘要器。"""

        async def on_reasoning(reasoning: str, text: str) -> None:
            await progress.thinking(reasoning=reasoning, text=text)

        chunks = _cooperative_chunks(chunks)

        message_id = turn_message(event).get("message_id")
        if not self.stream or self.client is None or not message_id or progress.message_id:
            result = await accumulate_stream(chunks, on_reasoning=on_reasoning)
            event_checkpoint()
            return result, False

        accumulator = _StreamAccumulator()

        async def text_tokens() -> AsyncGenerator[str, None]:
            async for chunk in chunks:
                accumulator.add(chunk)
                if isinstance(chunk, ReasoningDelta):
                    await on_reasoning(accumulator.result.reasoning, accumulator.result.text)
                elif isinstance(chunk, TextDelta) and chunk.text:
                    yield chunk.text

        tokens = text_tokens()
        failed = False
        try:
            # Tool-only turns must not create an empty visible card.
            first = await anext(tokens, None)
            if first is None:
                event_checkpoint()
                return accumulator.finish(), False

            async def replay() -> AsyncIterator[str]:
                yield first
                async for token in tokens:
                    yield token

            await mark_current_event_side_effect_started()
            async with current_event_effect():
                await self.client.stream_card(replay(), reply_to_message_id=message_id)
            event_checkpoint()
            return accumulator.finish(), True
        except BaseException:
            failed = True
            raise
        finally:
            cleanup_error: BaseException | None = None
            for source in (tokens, chunks):
                close = getattr(source, "aclose", None)
                if close is not None:
                    try:
                        await close()
                    except (Exception, asyncio.CancelledError) as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
            if cleanup_error is not None and not failed:
                raise cleanup_error

    async def _maybe_summarize(self, session_id: str, history: list[Message]) -> list[Message]:
        r"""历史超过 token 阈值时自动压缩；未配置阈值或未超阈值时原样返回。"""
        return await summarization_flow.maybe_summarize(self, session_id, history)

    async def _recover_interrupted_tool_calls(self, session_id: str, history: list[Message]) -> None:
        """Close tool calls left without results by a process that ended mid-effect."""
        repaired: list[Message] = []
        pending_tool_ids: set[str] = set()
        changed = False

        def close_pending_batch() -> None:
            nonlocal changed
            if not pending_tool_ids:
                return
            repaired.append(
                Message(
                    role="tool",
                    content=[
                        ToolResultPart(
                            tool_call_id=tool_id,
                            content=(
                                "The previous execution outcome is unknown. The tool was not replayed; "
                                "check the external system before trying again."
                            ),
                            is_error=True,
                        )
                        for tool_id in sorted(pending_tool_ids)
                    ],
                )
            )
            pending_tool_ids.clear()
            changed = True

        for message in history:
            if message.role == "assistant":
                close_pending_batch()
                pending_tool_ids = {part.id for part in message.content if isinstance(part, ToolUsePart)}
                repaired.append(message)
                continue
            if message.role == "tool" and pending_tool_ids:
                repaired.append(message)
                answered = {part.tool_call_id for part in message.content if isinstance(part, ToolResultPart)}
                pending_tool_ids.difference_update(answered)
                continue
            if pending_tool_ids:
                close_pending_batch()
            repaired.append(message)

        close_pending_batch()
        if not changed:
            return
        history[:] = repaired
        await self.store.set(session_id, history)

    async def _summarize_history(self, session_id: str, history: list[Message]) -> list[Message]:
        r"""把较早轮次压缩为一条摘要消息、保留最近 N 条原样，并持久化压缩后的历史。"""
        return await summarization_flow.summarize_history(self, session_id, history)

    async def _summarize(self, messages: list[Message]) -> str:
        r"""用注入的 `summarizer` 生成摘要，或回退到用本 backend 生成。"""
        return await summarization_flow.summarize_messages(self, messages)

    def _assistant_tool_message(self, result: StreamResult) -> Message:
        content: list = []
        if result.reasoning:
            content.append(ReasoningPart(text=result.reasoning))
        if result.text:
            content.append(TextPart(text=result.text))
        for call in result.tool_calls:
            content.append(ToolUsePart(id=call.id, name=call.name, arguments=parse_tool_arguments(call.arguments)))
        return Message(role="assistant", content=content, continuation_id=uuid4().hex)

    async def _dispatch_tool_calls(
        self, event: Any, session_id: str, history: list[Message], tool_calls: list[ToolCall], progress: _ProgressCard
    ) -> str | None:
        for call in tool_calls:
            event_checkpoint()
            self._assert_scheduled_lease(event)
            try:
                tool = self.registry.get(call.name)
            except KeyError:
                # The model requested an unknown / empty tool name. Feed an error tool result back so it
                # can recover on the next turn, instead of crashing the whole turn (no reply to the user).
                logging.getLogger("feishu").warning(
                    "agent: model requested unknown tool %r; returning error", call.name
                )
                await self._record_tool_result_part(
                    session_id,
                    history,
                    ToolResultPart(tool_call_id=call.id, content=f"error: unknown tool {call.name!r}", is_error=True),
                )
                continue
            if self.scheduled_store is not None and tool.name in {
                "create_scheduled_task",
                "pause_scheduled_task",
                "resume_scheduled_task",
                "delete_scheduled_task",
            }:
                from .toolkit.scheduled import preflight_scheduled_tool

                try:
                    preflight_scheduled_tool(
                        tool.name, parse_tool_arguments(call.arguments), self.scheduled_store, now=self._now()
                    )
                except (ValueError, TypeError, PermissionError, KeyError) as exc:
                    await self._record_tool_error(
                        history, session_id, call.id, f"scheduled task request rejected: {type(exc).__name__}"
                    )
                    continue
            auth_status = await self._preflight_authorization(event, session_id, history, call, tool, progress)
            if auth_status == "suspended":
                await self._mark_awaiting_authorization(session_id, history, tool_calls)
                return "authorization"
            if auth_status == "blocked":
                continue
            if tool.requires_approval:
                if await self._request_approval(event, session_id, history, call, progress):
                    # Keep history well-formed while suspended: every tool_use in this assistant turn needs a
                    # tool_result, else a later turn (if the user abandons the card) sends malformed history to
                    # the model. The real result replaces this placeholder on resume (_decide_and_resume).
                    await self._mark_awaiting_confirmation(session_id, history, tool_calls)
                    return "approval"  # suspend the turn awaiting confirmation
                continue  # fail-closed: no identifiable requester — error recorded, keep processing
            async with current_event_effect():
                result_part, suspended = await self._execute_tool_call(event, session_id, history, call, tool, progress)
                await self._record_tool_result_part(session_id, history, result_part)
            event_checkpoint()
            if suspended:
                await self._mark_awaiting_authorization(session_id, history, tool_calls)
                return "authorization"
        return None

    async def _preflight_authorization(
        self,
        event: Event,
        session_id: str,
        history: list[Message],
        call: ToolCall,
        tool: Tool,
        progress: _ProgressCard,
    ) -> Literal["suspended", "blocked"] | None:
        r"""工具执行 / 审批前检查用户授权；缺授权时先发授权卡片并挂起本轮。"""
        return await oauth_flow.preflight_authorization(self, event, session_id, history, call, tool, progress)

    async def _execute_tool_call(
        self,
        event: Event,
        session_id: str,
        history: list[Message],
        call: ToolCall,
        tool: Tool,
        progress: _ProgressCard,
    ) -> tuple[ToolResultPart, bool]:
        await progress.step(tool.name, description=tool.description)  # show the in-progress step for tools that run
        try:
            result = await self.registry.dispatch(call.name, parse_tool_arguments(call.arguments))
        except Exception as exc:  # noqa: BLE001 - a tool error must never crash the turn; report it back
            if current_event_job() is not None:
                raise
            logging.getLogger("feishu").warning(
                "agent: tool %s raised %s; feeding a sanitized error back to the model",
                call.name,
                type(exc).__name__,
            )
            result = ToolResult(
                ToolOutcome.FAILED,
                content=f"tool {call.name} failed with {type(exc).__name__}; see server logs for details",
                is_error=True,
            )
        content, is_error, tool_result = coerce_tool_result(result)
        if tool_result is not None and tool_result.outcome == ToolOutcome.NEEDS_USER_AUTH:
            if await self._request_authorization(event, session_id, history, call, tool_result, progress):
                return ToolResultPart(tool_call_id=call.id, content=_AWAITING_AUTHORIZATION_NOTE, is_error=False), True
            authorize_url = tool_result.authorize_url
            if authorize_url and await self._try_send_auth_card(event, authorize_url, progress):
                content = _AUTH_CARD_SENT_NOTE
        return ToolResultPart(tool_call_id=call.id, content=content, is_error=is_error), False

    async def _record_tool_result_part(
        self, session_id: str, history: list[Message], result_part: ToolResultPart
    ) -> None:
        if _replace_tool_result(_originating_tool_messages(history), result_part.tool_call_id, result_part):
            await self.store.set(session_id, history)
            return
        tool_msg = Message(role="tool", content=[result_part])
        history.append(tool_msg)
        await self.store.append(session_id, tool_msg)

    async def _continue_tool_calls_after(
        self,
        event: Event,
        session_id: str,
        history: list[Message],
        tool_call_id: str,
        progress: _ProgressCard,
    ) -> str | None:
        remaining = _tool_calls_after(_originating_tool_messages(history), tool_call_id)
        if not remaining:
            return None
        return await self._dispatch_tool_calls(event, session_id, history, remaining, progress)

    def _tool_context(self, event: Any) -> ToolContext:
        context = ToolContext(
            client=self.client,
            event=event,
            user=dict(event.user) if isinstance(event, ScheduledTurn) else {},
            user_tokens=self.user_tokens,
            authorize_url_builder=self.authorize_url_builder,
            shared_files=self.shared_files,
            payment_accounts=self.payment_accounts,
            memory_store=self.memory_store,
            memory_namespace=self.memory_namespace,
            timezone=(event.timezone or self.timezone) if isinstance(event, ScheduledTurn) else self.timezone,
            trusted_tenant_key=self.trusted_tenant_key,
        )
        context.resolved_tenant_key()
        return context

    async def _timezone_for_event(self, event: Any) -> str | None:
        if isinstance(event, ScheduledTurn):
            self._assert_scheduled_lease(event)
            return await self._tool_context(event).current_timezone()
        return await ToolContext(event=event, timezone=self.timezone).current_timezone()

    async def _system_for_event(self, event: Any, timezone: str | None) -> str | None:
        return await _dynamic_text(self.system, event, timezone)

    async def _turn_context_for_event(self, event: Any, timezone: str | None) -> str | None:
        return await _dynamic_text(self.turn_context, event, timezone)

    async def _register_inbound_files(self, event: Event) -> list[Any]:
        r"""把入站消息携带的全部文件登记为 SharedFile 句柄（支持多文件）；未配置存储或无文件时返回空列表。"""
        store = self._shared_files_store
        if store is None:
            return []
        from ..im.inbound import message_resources

        message = event.body.get("message") or {}
        # Default to capturing files only in 1:1 chats, so one member's file is never registered from a group.
        if self._shared_files_private_only and str(message.get("chat_type") or "") != "p2p":
            return []
        resources = message_resources(message)
        if not resources:
            return []
        user = current_tool_context().requesting_user()
        if not user:
            return []
        shared: list[Any] = []
        for resource in resources:
            try:
                shared.append(
                    await store.register(user, resource, message=message, ttl_seconds=self._shared_file_ttl_seconds)
                )
            except Exception:  # noqa: BLE001 — capturing an inbound file must never break the turn
                logging.getLogger("feishu").warning("failed to register an inbound shared file", exc_info=True)
        return shared

    async def _record_tool_error(
        self, history: list[Message], session_id: str, tool_call_id: str, content: str
    ) -> None:
        r"""向历史与会话存储追加一条错误工具结果，供审批 fail-closed 时让模型据此向用户说明。"""
        await self._record_tool_result_part(
            session_id, history, ToolResultPart(tool_call_id=tool_call_id, content=content, is_error=True)
        )

    async def _request_approval(
        self, event: Event, session_id: str, history: list[Message], call: ToolCall, progress: _ProgressCard
    ) -> bool:
        r"""为需审批的工具创建挂起审批并发送确认卡片；返回是否已挂起本轮。"""
        return await approval_flow.request_approval(self, event, session_id, history, call, progress)

    async def _request_authorization(
        self,
        event: Event,
        session_id: str,
        history: list[Message],
        call: ToolCall,
        result: ToolResult,
        progress: _ProgressCard | None = None,
    ) -> bool:
        r"""为缺少用户授权的工具创建挂起授权并发送授权卡片；返回是否已挂起本轮。"""
        return await oauth_flow.request_authorization(self, event, session_id, history, call, result, progress)

    def _build_authorize_url(
        self, user: Mapping[str, Any], scopes: tuple[str, ...], authorization: PendingAuthorization
    ) -> str | None:
        r"""构造产品授权 URL。"""
        return oauth_flow.build_authorize_url(self, user, scopes, authorization)

    async def _pin_referenced_files(self, arguments: Any) -> None:
        r"""把审批参数中引用到的分享文件句柄逐个 pin 缓存。"""
        await approval_flow.pin_referenced_files(self, arguments)

    async def _send_auth_card(self, event: Event, authorize_url: str, progress: _ProgressCard | None = None) -> bool:
        r"""向当前会话发送授权卡片。"""
        return await oauth_flow.send_auth_card(self, event, authorize_url, progress)

    async def _try_send_auth_card(
        self, event: Event, authorize_url: str, progress: _ProgressCard | None = None
    ) -> bool:
        r"""发送授权卡片的不抛错封装。"""
        return await oauth_flow.try_send_auth_card(self, event, authorize_url, progress)

    async def _mark_awaiting_confirmation(
        self, session_id: str, history: list[Message], tool_calls: list[ToolCall]
    ) -> None:
        r"""
        为本轮挂起审批 / 未及运行的工具调用补占位工具结果，保证「每个 tool_use 都有对应 tool_result」恒成立。

        若用户始终不点确认卡，下一轮会把历史重新发给模型——缺了 tool_result 的 tool_use 会违反工具调用协议。
        占位结果在恢复时由 `feishu.agent.loop.AgentEngine._decide_and_resume` 用真实结果原地替换。
        """
        await self._mark_awaiting_tool_results(session_id, history, tool_calls, _AWAITING_APPROVAL_NOTE)

    async def _mark_awaiting_authorization(
        self, session_id: str, history: list[Message], tool_calls: list[ToolCall]
    ) -> None:
        r"""为本轮挂起授权 / 未及运行的工具调用补占位工具结果。"""
        await self._mark_awaiting_tool_results(session_id, history, tool_calls, _AWAITING_AUTHORIZATION_NOTE)

    async def _mark_interrupted_tool_results(
        self, session_id: str, history: list[Message], tool_calls: list[ToolCall]
    ) -> None:
        r"""为被新消息打断的未完成工具调用补错误结果，避免下一轮历史中出现悬空 tool_use。"""
        await self._mark_awaiting_tool_results(session_id, history, tool_calls, _INTERRUPTED_TOOL_NOTE, is_error=True)

    async def _mark_awaiting_tool_results(
        self,
        session_id: str,
        history: list[Message],
        tool_calls: list[ToolCall],
        note: str,
        *,
        is_error: bool = False,
    ) -> None:
        r"""为尚未写入 tool_result 的 tool_use 追加占位结果，保持历史符合工具调用协议。"""
        answered = {
            part.tool_call_id
            for msg in _originating_tool_messages(history)
            if msg.role == "tool"
            for part in msg.content
            if isinstance(part, ToolResultPart)
        }
        placeholders: list[ContentPart] = [
            ToolResultPart(tool_call_id=call.id, content=note, is_error=is_error)
            for call in tool_calls
            if call.id not in answered
        ]
        if not placeholders:
            return
        message = Message(role="tool", content=placeholders)
        history.append(message)
        await self.store.append(session_id, message)

    async def handle_card_action(self, event: Event) -> dict[str, Any]:
        r"""处理审批卡片回传：同步 ACK，后台完成决策、工具执行、历史续跑与卡片更新。"""
        if not self._accepting_events:
            return {"toast": {"type": "info", "content": "Agent is shutting down. Please retry shortly."}}
        return await approval_flow.handle_card_action(self, event)

    async def resume_authorization(self, authorization_id: str, *, user: Mapping[str, Any] | None = None) -> str:
        r"""在 OAuth 回调成功保存用户 token 后，恢复一次挂起授权对应的原工具调用。"""
        if not self._accepting_events:
            raise RuntimeError("agent is shutting down and cannot resume authorization")
        return await oauth_flow.resume_authorization(self, authorization_id, user=user)

    async def _notify_authorization_resume_problem(self, authorization: PendingAuthorization, text: str) -> None:
        r"""在 OAuth 回调无法恢复已知 pending authorization 时，尽力向聊天里反馈状态。"""
        await oauth_flow.notify_authorization_resume_problem(self, authorization, text)

    async def _remove_authorization_card(self, authorization: PendingAuthorization) -> None:
        r"""授权完成后尽力清理独立 OAuth 授权卡片。"""
        await oauth_flow.remove_authorization_card(self, authorization)

    def _event_from_pending_authorization(self, authorization: PendingAuthorization) -> Any:
        return oauth_flow.event_from_pending_authorization(authorization)

    def _tool_description(self, tool_name: str) -> str | None:
        try:
            return self.registry.get(tool_name).description
        except KeyError:
            return None

    def _spawn_background(self, coro: Any) -> None:
        r"""把协程作为后台任务运行（保留引用以防被 GC）；其异常已在任务内部自行捕获。"""
        if not self._accepting_events:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            return
        task = asyncio.ensure_future(coro)
        self.track_background_task(task)

    async def _decide_and_resume(
        self,
        event: Event,
        approval: PendingApproval,
        decision: Literal["approve", "reject"],
        value: dict[str, Any],
        card_message_id: str | None,
    ) -> None:
        r"""后台执行审批决定，并在需要时恢复原模型轮次。"""
        await approval_flow.decide_and_resume(self, event, approval, decision, value, card_message_id)

    def _default_approval_card(self, approval: PendingApproval) -> dict[str, Any]:
        return approval_flow.default_approval_card(approval)

    def _build_approval_card(self, approval: PendingApproval) -> dict[str, Any]:
        if self.scheduled_store is not None and approval.tool_name in {
            "create_scheduled_task",
            "pause_scheduled_task",
            "resume_scheduled_task",
            "delete_scheduled_task",
        }:
            from .toolkit.scheduled import scheduled_approval_card

            return scheduled_approval_card(approval, self.scheduled_store)
        return self._product_approval_card_builder(approval)

    def _default_decided_card(
        self, approval: PendingApproval, decision: str, outcome: ApprovalOutcome
    ) -> dict[str, Any]:
        return approval_flow.default_decided_card(approval, decision, outcome)

    async def _finalize(self, event: Any, text: str) -> None:
        if isinstance(event, ScheduledTurn):
            self._assert_scheduled_lease(event)
            if self.client is None:
                raise RuntimeError("scheduled turn has no delivery client")
            await mark_current_event_side_effect_started()
            async with current_event_effect():
                response = await self.client.im.send(event.chat_id, text, msg_type="text", receive_id_type="chat_id")
            if not _message_id_from_response(response):
                from .scheduled_policy import ScheduledDeliveryUnknown

                raise ScheduledDeliveryUnknown("scheduled text response has no message ID")
            return
        message = event.body.get("message") or {}
        message_id = message.get("message_id")
        if self.stream and self.client is not None:
            await mark_current_event_side_effect_started()
            await self._finalize_stream(event, text)
            return
        if self.client is not None and message_id:
            await mark_current_event_side_effect_started()
            async with current_event_effect():
                await self.client.im.reply(message_id, text, msg_type="text")

    async def _finalize_stream(self, event: Event, text: str) -> None:
        message = event.body.get("message") or {}
        message_id = message.get("message_id")
        # Mirror the non-stream _finalize: reply in-thread to the inbound message; skip if absent.
        if not message_id:
            return

        async def _one_token() -> AsyncIterator[str]:
            yield text

        await mark_current_event_side_effect_started()
        async with current_event_effect():
            await self.client.stream_card(_one_token(), reply_to_message_id=message_id)  # type: ignore[union-attr]


def _originating_tool_messages(history: list[Message]) -> list[Message]:
    r"""Restrict reused tool ids to the current or persisted originating assistant batch."""
    origin = current_tool_context().continuation_id
    if not origin:
        return history
    for start, message in enumerate(history):
        if message.role == "assistant" and message.continuation_id == origin:
            end = start + 1
            while end < len(history) and history[end].role == "tool":
                end += 1
            # The slice retains the same Message objects; result-part replacement mutates those objects,
            # and the caller persists the full history, not this lookup slice.
            return history[start:end]
    return []


def _message_text(message: Message) -> str:
    r"""拼接一条消息里的所有文本片段（用于 /clear 等命令检测）。"""
    parts = [
        part.text
        for part in (getattr(message, "content", None) or [])
        if isinstance(getattr(part, "text", None), str) and part.text
    ]
    return "\n".join(parts).strip()
