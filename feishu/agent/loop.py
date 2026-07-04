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
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal

from ..events.envelope import Event
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
    LlmBackend,
    Message,
    MessageStop,
    ReasoningDelta,
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
from .progress import (
    ProgressSnapshot,
    _ProgressCard,
)
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
    text = ""
    reasoning = ""
    by_index: dict[int, _Accum] = {}
    stop_reason = StopReason.OTHER
    usage: dict[str, int] | None = None
    async for chunk in chunks:
        if isinstance(chunk, TextDelta):
            text += chunk.text
        elif isinstance(chunk, ReasoningDelta):
            reasoning += chunk.text
            if on_reasoning is not None:
                update = on_reasoning(reasoning, text)
                if inspect.isawaitable(update):
                    await update
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
        ToolCall(id=acc.id or "", name=acc.name, arguments=acc.arguments)
        for _, acc in sorted(by_index.items())
        if acc.name  # drop malformed tool-call slots that never received a name (some models emit empties)
    ]
    return StreamResult(
        text=text,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        usage=usage,
        reasoning=reasoning,
    )


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
        now: Callable[[], float] | None = None,
        timezone: str | Callable[..., Any] | None = None,
        interrupted_progress_text: str = "已被更新的消息打断。",
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
        self.authorizations: PendingAuthorizationStore = authorizations or InMemoryPendingAuthorizationStore()
        self.approval_engine: ApprovalEngine = approval_engine or DefaultApprovalEngine(approvals=self.approvals)
        self._approval_card_builder = approval_card_builder or approval_flow.default_approval_card
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
        self._now = now or _default_now
        self.timezone = timezone
        self._interrupted_progress_text = interrupted_progress_text
        self.stream = stream
        self.backend_kwargs = backend_kwargs
        self._bg_tasks: set[asyncio.Task[None]] = set()
        self._active_turns: dict[str, _ActiveTurn] = {}
        self._superseded_turns: set[asyncio.Task[Any]] = set()
        # Per-session lock: serialize each session's history read-modify-write (a new message and a background
        # approval-resume for the same chat must not interleave and clobber each other's turns).
        self._session_locks: dict[str, asyncio.Lock] = {}

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        r"""返回该会话的串行化锁（不存在则创建）。在单事件循环中 get/setdefault 之间无 await，故创建是安全的。"""
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        return lock

    def _begin_session_turn(
        self, session_id: str, progress: _ProgressCard, *, interrupt_previous: bool = False
    ) -> asyncio.Task[Any] | None:
        r"""把当前任务登记为该会话的活跃轮次，并可选择中断上一轮。"""
        task = asyncio.current_task()
        if task is None:
            return None
        previous = self._active_turns.get(session_id)
        if interrupt_previous and previous is not None and previous.task is not task and not previous.task.done():
            self._superseded_turns.add(previous.task)
            previous.task.cancel()
        self._active_turns[session_id] = _ActiveTurn(task=task, progress=progress)
        return task

    def _end_session_turn(self, session_id: str, task: asyncio.Task[Any] | None) -> None:
        if task is None:
            return
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
        session_id = session_id_for(event)
        user_msg = user_message_from_event(event)
        progress = _ProgressCard(self, event)
        active_task = self._begin_session_turn(session_id, progress, interrupt_previous=True)
        try:
            # Serialize per session: hold the session lock across the whole read-modify-write + loop so a
            # concurrent message (or a background approval-resume) for the same chat can't load stale history
            # and clobber a turn.
            with use_tool_context(self._tool_context(event)):
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
                    shared = await self._register_inbound_files(event)
                    if shared:
                        # Make the model aware of the just-shared files (by opaque handle only — never bytes).
                        user_msg.content.append(TextPart(text=shared_files_note(shared)))
                    history.append(user_msg)
                    await self.store.set(session_id, history)
                    await self._loop(event, session_id, history, progress=progress)
        except asyncio.CancelledError:
            if self._turn_was_superseded(active_task):
                await self._finalize_interrupted_progress(progress)
                return
            raise
        finally:
            self._end_session_turn(session_id, active_task)

    async def _loop(
        self, event: Event, session_id: str, history: list[Message], progress: _ProgressCard | None = None
    ) -> None:
        progress = progress or _ProgressCard(self, event)
        result = None
        active_tool_calls: list[ToolCall] | None = None
        # Compact the history at a token threshold (collapse old turns into one summary message) BEFORE the
        # model call, so a long session keeps a stable, cacheable prefix instead of paying a growing prompt.
        try:
            history = await self._maybe_summarize(session_id, history)
            await progress.start()
            timezone = await self._timezone_for_event(event)
            for _ in range(self.max_iterations):
                system = await self._system_for_event(event, timezone)
                messages = _messages_with_turn_context(history, await self._turn_context_for_event(event, timezone))
                result = await self._accumulate_stream_with_progress(
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
                    history.append(assistant)
                    await self.store.append(session_id, assistant)
                    active_tool_calls = result.tool_calls
                    suspension = await self._dispatch_tool_calls(
                        event, session_id, history, result.tool_calls, progress
                    )
                    active_tool_calls = None
                    if suspension:
                        await progress.finalize(_suspension_progress_note(suspension))
                        return  # authorization / approval suspended the turn
                    continue
                assistant = Message(role="assistant", content=[TextPart(text=result.text)])
                history.append(assistant)
                await self.store.append(session_id, assistant)
                # Replace the progress card in place with the answer; only send a separate reply if there was none.
                if not await progress.finalize(result.text):
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
            if not await progress.finalize(fallback):
                await self._finalize(event, fallback)
        except asyncio.CancelledError:
            if active_tool_calls:
                await self._mark_interrupted_tool_results(session_id, history, active_tool_calls)
            raise

    async def _accumulate_stream_with_progress(
        self, chunks: AsyncIterator[StreamChunk], progress: _ProgressCard
    ) -> StreamResult:
        r"""归并一轮模型流；若配置了进度摘要器，则用近期 reasoning 原地更新进度卡片。"""

        async def on_reasoning(reasoning: str, text: str) -> None:
            await progress.thinking(reasoning=reasoning, text=text)

        return await accumulate_stream(chunks, on_reasoning=on_reasoning)

    async def _maybe_summarize(self, session_id: str, history: list[Message]) -> list[Message]:
        r"""历史超过 token 阈值时自动压缩；未配置阈值或未超阈值时原样返回。"""
        return await summarization_flow.maybe_summarize(self, session_id, history)

    async def _summarize_history(self, session_id: str, history: list[Message]) -> list[Message]:
        r"""把较早轮次压缩为一条摘要消息、保留最近 N 条原样，并持久化压缩后的历史。"""
        return await summarization_flow.summarize_history(self, session_id, history)

    async def _summarize(self, messages: list[Message]) -> str:
        r"""用注入的 `summarizer` 生成摘要，或回退到用本 backend 生成。"""
        return await summarization_flow.summarize_messages(self, messages)

    def _assistant_tool_message(self, result: StreamResult) -> Message:
        content: list = []
        if result.text:
            content.append(TextPart(text=result.text))
        for call in result.tool_calls:
            content.append(ToolUsePart(id=call.id, name=call.name, arguments=parse_tool_arguments(call.arguments)))
        return Message(role="assistant", content=content)

    async def _dispatch_tool_calls(
        self, event: Event, session_id: str, history: list[Message], tool_calls: list[ToolCall], progress: _ProgressCard
    ) -> str | None:
        for call in tool_calls:
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
            result_part, suspended = await self._execute_tool_call(event, session_id, history, call, tool, progress)
            await self._record_tool_result_part(session_id, history, result_part)
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
            if authorize_url and await self._try_send_auth_card(event, authorize_url):
                content = _AUTH_CARD_SENT_NOTE
        return ToolResultPart(tool_call_id=call.id, content=content, is_error=is_error), False

    async def _record_tool_result_part(
        self, session_id: str, history: list[Message], result_part: ToolResultPart
    ) -> None:
        if _replace_tool_result(history, result_part.tool_call_id, result_part):
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
        remaining = _tool_calls_after(history, tool_call_id)
        if not remaining:
            return None
        return await self._dispatch_tool_calls(event, session_id, history, remaining, progress)

    def _tool_context(self, event: Event) -> ToolContext:
        return ToolContext(
            client=self.client,
            event=event,
            user_tokens=self.user_tokens,
            authorize_url_builder=self.authorize_url_builder,
            shared_files=self.shared_files,
            payment_accounts=self.payment_accounts,
            timezone=self.timezone,
        )

    async def _timezone_for_event(self, event: Event) -> str | None:
        return await ToolContext(event=event, timezone=self.timezone).current_timezone()

    async def _system_for_event(self, event: Event, timezone: str | None) -> str | None:
        return await _dynamic_text(self.system, event, timezone)

    async def _turn_context_for_event(self, event: Event, timezone: str | None) -> str | None:
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

    async def _send_auth_card(self, event: Event, authorize_url: str) -> bool:
        r"""向当前会话发送授权卡片。"""
        return await oauth_flow.send_auth_card(self, event, authorize_url)

    async def _try_send_auth_card(self, event: Event, authorize_url: str) -> bool:
        r"""发送授权卡片的不抛错封装。"""
        return await oauth_flow.try_send_auth_card(self, event, authorize_url)

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
            for msg in history
            if msg.role == "tool"
            for part in msg.content
            if isinstance(part, ToolResultPart)
        }
        placeholders: list[TextPart | ToolUsePart | ToolResultPart] = [
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
        return await approval_flow.handle_card_action(self, event)

    async def resume_authorization(self, authorization_id: str, *, user: Mapping[str, Any] | None = None) -> str:
        r"""在 OAuth 回调成功保存用户 token 后，恢复一次挂起授权对应的原工具调用。"""
        return await oauth_flow.resume_authorization(self, authorization_id, user=user)

    async def _notify_authorization_resume_problem(self, authorization: PendingAuthorization, text: str) -> None:
        r"""在 OAuth 回调无法恢复已知 pending authorization 时，尽力向聊天里反馈状态。"""
        await oauth_flow.notify_authorization_resume_problem(self, authorization, text)

    async def _remove_authorization_card(self, authorization: PendingAuthorization) -> None:
        r"""授权完成后尽力清理独立 OAuth 授权卡片。"""
        await oauth_flow.remove_authorization_card(self, authorization)

    def _event_from_pending_authorization(self, authorization: PendingAuthorization) -> Event:
        return oauth_flow.event_from_pending_authorization(authorization)

    def _tool_description(self, tool_name: str) -> str | None:
        try:
            return self.registry.get(tool_name).description
        except KeyError:
            return None

    def _spawn_background(self, coro: Any) -> None:
        r"""把协程作为后台任务运行（保留引用以防被 GC）；其异常已在任务内部自行捕获。"""
        task = asyncio.ensure_future(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

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

    def _default_decided_card(
        self, approval: PendingApproval, decision: str, outcome: ApprovalOutcome
    ) -> dict[str, Any]:
        return approval_flow.default_decided_card(approval, decision, outcome)

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


def _message_text(message: Message) -> str:
    r"""拼接一条消息里的所有文本片段（用于 /clear 等命令检测）。"""
    parts = [
        part.text
        for part in (getattr(message, "content", None) or [])
        if isinstance(getattr(part, "text", None), str) and part.text
    ]
    return "\n".join(parts).strip()
