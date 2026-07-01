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
import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal
from uuid import uuid4

from ..auth import user_from_identity_keys, user_identity_keys
from ..events.envelope import Event
from .approval import ApprovalEngine, ApprovalOutcome, ApprovalStatus, DefaultApprovalEngine
from .context import ToolContext, current_tool_context, use_tool_context
from .integrity import payload_sha256
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
)
from .result import ToolOutcome, ToolResult
from .session import (
    ClaimResult,
    InMemoryPendingApprovalStore,
    InMemoryPendingAuthorizationStore,
    InMemorySessionStore,
    PendingApproval,
    PendingApprovalStore,
    PendingAuthorization,
    PendingAuthorizationStore,
    SessionStore,
)
from .tools import Tool, ToolRegistry

if TYPE_CHECKING:
    from ..client import FeishuClient


AWAITING_APPROVAL_PROGRESS_TEXT = "Waiting for user confirmation."
AWAITING_AUTHORIZATION_PROGRESS_TEXT = "Waiting for user authorization."


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


@dataclass(frozen=True)
class ProgressSnapshot:
    r"""
    供产品侧生成可见进度文案的短暂快照。

    `reasoning` 可包含模型流式输出的原始近期 thinking/reasoning 片段。SDK 只在内存中传给
    `progress_summarizer`，不落会话历史、审批审计或日志；产品侧应把它摘要为可展示的一句话。
    """

    phase: str
    reasoning: str = ""
    text: str = ""
    tool_name: str | None = None
    tool_description: str | None = None
    elapsed_seconds: float = 0.0


async def accumulate_stream(chunks: AsyncIterator[StreamChunk]) -> StreamResult:
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
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    by_index: dict[int, _Accum] = {}
    stop_reason = StopReason.OTHER
    usage: dict[str, int] | None = None
    async for chunk in chunks:
        if isinstance(chunk, TextDelta):
            text_parts.append(chunk.text)
        elif isinstance(chunk, ReasoningDelta):
            reasoning_parts.append(chunk.text)
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
        text="".join(text_parts),
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        usage=usage,
        reasoning="".join(reasoning_parts),
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
            `(user_mapping, scopes, pending_authorization=None) -> str | None`。SDK 工具缺少用户授权时会传入
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
        system: 系统提示词。
        stream: 未使用进度卡片时是否以流式卡片回复。为 `True` 时经 `client.stream_card` 输出，否则调用
            `client.im.reply`。
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
        clear_reply: str = "Conversation history cleared.",
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
        self.authorizations: PendingAuthorizationStore = authorizations or InMemoryPendingAuthorizationStore()
        self.approval_engine: ApprovalEngine = approval_engine or DefaultApprovalEngine(approvals=self.approvals)
        self._approval_card_builder = approval_card_builder or self._default_approval_card
        self._decided_card_builder = decided_card_builder or self._default_decided_card
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
        self.stream = stream
        self.backend_kwargs = backend_kwargs
        self._bg_tasks: set[asyncio.Task[None]] = set()
        # Per-session lock: serialize each session's history read-modify-write (a new message and a background
        # approval-resume for the same chat must not interleave and clobber each other's turns).
        self._session_locks: dict[str, asyncio.Lock] = {}

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        r"""返回该会话的串行化锁（不存在则创建）。在单事件循环中 get/setdefault 之间无 await，故创建是安全的。"""
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        return lock

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
        with use_tool_context(self._tool_context(event)):
            session_id = session_id_for(event)
            user_msg = user_message_from_event(event)
            # Serialize per session: hold the session lock across the whole read-modify-write + loop so a
            # concurrent message (or a background approval-resume) for the same chat can't load stale history
            # and clobber a turn.
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
                    reply = (self._compact_reply or _default_compact_reply)(len(existing), len(compacted))
                    await self._finalize(event, reply)
                    return
                history = await self.store.get(session_id)
                shared = await self._register_inbound_files(event)
                if shared:
                    # Make the model aware of the just-shared files (by opaque handle only — never bytes).
                    user_msg.content.append(TextPart(text=_shared_files_note(shared)))
                history.append(user_msg)
                await self.store.set(session_id, history)
                await self._loop(event, session_id, history)

    async def _loop(
        self, event: Event, session_id: str, history: list[Message], progress: _ProgressCard | None = None
    ) -> None:
        progress = progress or _ProgressCard(self, event)
        result = None
        # Compact the history at a token threshold (collapse old turns into one summary message) BEFORE the
        # model call, so a long session keeps a stable, cacheable prefix instead of paying a growing prompt.
        history = await self._maybe_summarize(session_id, history)
        await progress.start()
        for _ in range(self.max_iterations):
            result = await self._accumulate_stream_with_progress(
                self.backend.stream(
                    messages=history,
                    tools=self.registry.specs(),
                    system=self.system,
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
                suspension = await self._dispatch_tool_calls(event, session_id, history, result.tool_calls, progress)
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

    async def _accumulate_stream_with_progress(
        self, chunks: AsyncIterator[StreamChunk], progress: _ProgressCard
    ) -> StreamResult:
        r"""归并一轮模型流；若配置了进度摘要器，则用近期 reasoning 原地更新进度卡片。"""
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
                await progress.thinking(reasoning=reasoning, text=text)
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
            if acc.name
        ]
        return StreamResult(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
            reasoning=reasoning,
        )

    async def _maybe_summarize(self, session_id: str, history: list[Message]) -> list[Message]:
        r"""
        历史超过 token 阈值时自动压缩，使长会话维持稳定、可被前缀缓存命中的 prefix（仅在跨阈值时一次 miss，而非
        随滑动窗口每轮失配）。未配置阈值（0）或未超阈值时原样返回。手动触发见 [feishu.agent.loop.Agent.run][]
        的 compact 命令。
        """
        threshold = self._summarize_threshold_tokens
        if not threshold or _estimate_tokens(history) <= threshold:
            return history
        return await self._summarize_history(session_id, history)

    async def _summarize_history(self, session_id: str, history: list[Message]) -> list[Message]:
        r"""
        把较早轮次压缩为一条摘要消息、保留最近 N 条原样，并持久化压缩后的历史；返回压缩后的历史。

        没有可压缩的旧消息、或摘要失败 / 为空时**原样返回且无副作用**（绝不打断本轮）。自动阈值压缩与手动
        compact 命令共用此方法。
        """
        keep = max(0, self._summarize_keep_recent)
        old = history[:-keep] if keep else list(history)
        recent = history[-keep:] if keep else []
        if not old:
            return history
        try:
            summary = await self._summarize(old)
        except Exception:  # noqa: BLE001 - summarization must never break the turn
            logging.getLogger("feishu").exception("history summarization failed; keeping full history")
            return history
        if not summary.strip():
            return history
        summary_msg = Message(role="user", content=[TextPart(text=f"{self._summary_prefix}\n{summary.strip()}")])
        compacted = [summary_msg, *recent]
        await self.store.set(session_id, compacted)
        logging.getLogger("feishu").info(
            "compacted %d old messages into 1 (history %d -> %d messages)", len(old), len(history), len(compacted)
        )
        return compacted

    async def _summarize(self, messages: list[Message]) -> str:
        r"""用注入的 `summarizer` 生成摘要，或回退到用本 backend 生成。"""
        if self._summarizer is not None:
            return await self._summarizer(messages)
        convo = _render_messages_for_summary(messages)
        prompt = [Message(role="user", content=[TextPart(text=f"{self._summary_instruction}\n\n{convo}")])]
        result = await accumulate_stream(
            self.backend.stream(messages=prompt, tools=(), system=None, **self.backend_kwargs)
        )
        return result.text

    def _assistant_tool_message(self, result: StreamResult) -> Message:
        content: list = []
        if result.text:
            content.append(TextPart(text=result.text))
        for call in result.tool_calls:
            content.append(ToolUsePart(id=call.id, name=call.name, arguments=_loads(call.arguments)))
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
            auth_status = await self._preflight_authorization(event, session_id, history, call, tool)
            if auth_status == "suspended":
                await self._mark_awaiting_authorization(session_id, history, tool_calls)
                return "authorization"
            if auth_status == "blocked":
                continue
            if tool.requires_approval:
                if await self._request_approval(event, session_id, history, call):
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
        self, event: Event, session_id: str, history: list[Message], call: ToolCall, tool: Tool
    ) -> Literal["suspended", "blocked"] | None:
        r"""工具执行 / 审批前检查用户授权；缺授权时先发授权卡片并挂起本轮。"""
        scopes = tuple(tool.auth_scopes)
        if not scopes or await current_tool_context().has_user_auth(scopes):
            return None
        missing_auth = ToolResult(
            ToolOutcome.NEEDS_USER_AUTH,
            content="user authorization required",
            auth_scopes=scopes,
            is_error=True,
        )
        if await self._request_authorization(event, session_id, history, call, missing_auth):
            return "suspended"
        await self._record_tool_result_part(
            session_id,
            history,
            ToolResultPart(
                tool_call_id=call.id,
                content="user authorization required, but I could not send an authorization card",
                is_error=True,
            ),
        )
        return "blocked"

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
            result = await self.registry.dispatch(call.name, _loads(call.arguments))
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
        content, is_error, tool_result = _coerce_tool_result(result)
        if tool_result is not None and tool_result.outcome == ToolOutcome.NEEDS_USER_AUTH:
            if await self._request_authorization(event, session_id, history, call, tool_result):
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
        )

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

    async def _request_approval(self, event: Event, session_id: str, history: list[Message], call: ToolCall) -> bool:
        r"""
        为需审批的工具创建挂起审批并发送确认卡片；返回是否已挂起本轮（fail-closed 时返回 `False`）。

        把审批绑定到发起人（`owner_user_keys`）——确认与执行均据此限定 / 以发起人身份进行（见
        [feishu.agent.loop.Agent.handle_card_action][] / `feishu.agent.loop.Agent._decide_and_resume`）。
        **fail-closed 三道闸**：① 无法识别发起人身份；② 无客户端 / 无 chat 可发卡；③ 落库或发卡失败——任一
        情况都不会留下用户点不到的「悬挂审批」让模型轮次空挂，改为记录一条工具错误并放行主循环让模型回复。

        持久化**先于**发卡：先 `on_request` 落库再发卡，确保任何点击都能在存储里找到挂起审批（避免「卡已送达
        但 pending 尚未落库」窗口里点击得到 *no pending* 的反向竞态）。发卡失败时显式 `on_cancel` 撤销刚落库的
        挂起审批，绝不留悬挂；引用文件仅在卡片确实送达后才 pin。落库后发卡前若进程崩溃，残留的 pending 因卡片
        从未送达而无人可点（良性），并会在 TTL 到期后被清理。
        """
        arguments = _loads(call.arguments)
        initiator = current_tool_context().requesting_user()
        owner_user_keys = user_identity_keys(initiator)
        message = event.body.get("message") or {}
        chat_id = message.get("chat_id")
        if not owner_user_keys:
            await self._record_tool_error(
                history,
                session_id,
                call.id,
                "cannot create a confirmable write: the requesting user could not be identified",
            )
            return False
        if self.client is None or not chat_id:
            await self._record_tool_error(
                history,
                session_id,
                call.id,
                "cannot create a confirmable write: no chat is available for the confirmation card",
            )
            return False
        approval = PendingApproval(
            approval_id=uuid4().hex,
            session_id=session_id,
            tool_call_id=call.id,
            tool_name=call.name,
            arguments=arguments,
            payload_sha256=payload_sha256(arguments),
            owner_user_keys=owner_user_keys,
            tenant_key=getattr(event, "tenant_key", None),
            chat_id=chat_id,
            created_message_id=message.get("message_id"),
            created_event_id=getattr(event, "event_id", None) or None,
            created_at=int(time.time()),
        )
        # Build the card (pure, local) BEFORE persisting so a builder error can't strand a pending.
        card = self._approval_card_builder(approval)
        # Persist FIRST so any click resolves against a stored pending (no "card delivered but pending not yet
        # stored" window). A persistence failure means nothing was committed — record an error and let the model reply.
        try:
            await self.approval_engine.on_request(approval)
        except Exception:  # noqa: BLE001 — could not record the pending → no confirmable write; let the model respond
            logging.getLogger("feishu").warning("failed to persist pending approval; write not started", exc_info=True)
            await self._record_tool_error(
                history, session_id, call.id, "could not record the confirmation request; the write was not started"
            )
            return False
        # Then deliver the card. If delivery fails, explicitly cancel the just-stored pending so nothing dangles.
        try:
            await self.client.im.send(chat_id, card, msg_type="interactive", receive_id_type="chat_id")
        except Exception:  # noqa: BLE001 — undeliverable card → cancel the pending, then let the model respond
            logging.getLogger("feishu").warning(
                "failed to send approval card; cancelling pending %s", approval.approval_id, exc_info=True
            )
            try:
                await self.approval_engine.on_cancel(approval.approval_id)
            except Exception:  # noqa: BLE001 — best-effort cleanup; an uncancelled pending will TTL-expire
                logging.getLogger("feishu").warning(
                    "failed to cancel pending %s after card send failure", approval.approval_id, exc_info=True
                )
            await self._record_tool_error(
                history, session_id, call.id, "failed to send the confirmation card; the write was not started"
            )
            return False
        # Card delivered and pending live — pin referenced files so the post-approval consume can't fail because
        # Feishu aged out the source during the confirmation round-trip.
        await self._pin_referenced_files(arguments)
        return True

    async def _request_authorization(
        self, event: Event, session_id: str, history: list[Message], call: ToolCall, result: ToolResult
    ) -> bool:
        r"""
        为缺少用户授权的工具创建挂起授权并发送授权卡片；返回是否已挂起本轮。

        与审批一致，挂起记录先于卡片送达落库；OAuth callback 成功后，产品调用
        [feishu.agent.loop.Agent.resume_authorization][] 恢复原工具调用。
        """
        initiator = current_tool_context().requesting_user()
        owner_user_keys = user_identity_keys(initiator)
        message = event.body.get("message") or {}
        chat_id = message.get("chat_id")
        if not owner_user_keys or self.client is None or not chat_id or self._auth_card_builder is None:
            return False
        authorization = PendingAuthorization(
            authorization_id=uuid4().hex,
            session_id=session_id,
            tool_call_id=call.id,
            tool_name=call.name,
            arguments=_loads(call.arguments),
            scopes=tuple(result.auth_scopes),
            owner_user_keys=owner_user_keys,
            tenant_key=getattr(event, "tenant_key", None),
            chat_id=chat_id,
            created_message_id=message.get("message_id"),
            created_event_id=getattr(event, "event_id", None) or None,
            created_at=int(time.time()),
        )
        authorize_url = self._build_authorize_url(initiator, authorization.scopes, authorization)
        if not authorize_url:
            return False
        try:
            card = self._auth_card_builder(authorize_url)
        except Exception:  # noqa: BLE001 - a product card builder error should fall back to the old auth path
            logging.getLogger("feishu").warning("failed to build auth card", exc_info=True)
            return False
        try:
            await self.authorizations.put(authorization)
        except Exception:  # noqa: BLE001 - no persisted pending means callback cannot resume safely
            logging.getLogger("feishu").warning("failed to persist pending authorization", exc_info=True)
            return False
        try:
            await self.client.im.send(chat_id, card, msg_type="interactive", receive_id_type="chat_id")
        except Exception:  # noqa: BLE001 - undeliverable card -> cancel the pending, then fall back
            logging.getLogger("feishu").warning(
                "failed to send auth card; cancelling pending %s", authorization.authorization_id, exc_info=True
            )
            try:
                await self.authorizations.complete(authorization.authorization_id, outcome="cancelled")
            except Exception:  # noqa: BLE001 - best-effort cleanup; an uncancelled pending will TTL-expire
                logging.getLogger("feishu").warning(
                    "failed to cancel pending authorization %s after card send failure",
                    authorization.authorization_id,
                    exc_info=True,
                )
            return False
        return True

    def _build_authorize_url(
        self, user: Mapping[str, Any], scopes: tuple[str, ...], authorization: PendingAuthorization
    ) -> str | None:
        builder = self.authorize_url_builder
        if builder is None:
            return None
        if _accepts_positional_arguments(builder, 3):
            return builder(user, scopes, authorization)
        return builder(user, scopes)

    async def _pin_referenced_files(self, arguments: Any) -> None:
        r"""把审批参数中引用到的分享文件（`sf_…` 句柄）逐个 pin 缓存；失败只记录，绝不影响审批流程。"""
        resolver = self.shared_files
        if resolver is None:
            return
        user = current_tool_context().requesting_user()
        if not user:
            return
        for file_id in _collect_shared_file_ids(arguments):
            try:
                await resolver.pin(user, file_id)
            except Exception:  # noqa: BLE001 — pinning is best-effort durability, never fatal
                logging.getLogger("feishu").debug("pin-on-approval failed for %s", file_id, exc_info=True)

    async def _send_auth_card(self, event: Event, authorize_url: str) -> bool:
        r"""
        缺少用户授权时，向当前会话发送一张带「去授权」按钮的交互卡片，替代把原始链接塞进文本回复。

        卡片样式由产品注入的 `auth_card_builder`（签名 `(authorize_url) -> dict`）决定；未注入构造器、
        无客户端或事件缺少 `chat_id` 时不发送并返回 `False`，调用方据此回退为在文本中附带链接。
        """
        if self._auth_card_builder is None or self.client is None or not authorize_url:
            return False
        message = event.body.get("message") or {}
        chat_id = message.get("chat_id")
        if not chat_id:
            return False
        card = self._auth_card_builder(authorize_url)
        await self.client.im.send(chat_id, card, msg_type="interactive", receive_id_type="chat_id")
        return True

    async def _try_send_auth_card(self, event: Event, authorize_url: str) -> bool:
        r"""发送授权卡片的不抛错封装：发送失败只记日志并返回 `False`，使调用方仍能落工具结果（避免悬挂 tool_use）。"""
        try:
            return await self._send_auth_card(event, authorize_url)
        except Exception:  # noqa: BLE001 — auth-card delivery must never crash the turn or drop the tool result
            logging.getLogger("feishu").warning("failed to send auth card; returning the URL inline", exc_info=True)
            return False

    async def _mark_awaiting_confirmation(
        self, session_id: str, history: list[Message], tool_calls: list[ToolCall]
    ) -> None:
        r"""
        为本轮挂起审批 / 未及运行的工具调用补占位工具结果，保证「每个 tool_use 都有对应 tool_result」恒成立。

        若用户始终不点确认卡，下一轮会把历史重新发给模型——缺了 tool_result 的 tool_use 会违反工具调用协议。
        占位结果在恢复时由 `feishu.agent.loop.Agent._decide_and_resume` 用真实结果原地替换。
        """
        await self._mark_awaiting_tool_results(session_id, history, tool_calls, _AWAITING_APPROVAL_NOTE)

    async def _mark_awaiting_authorization(
        self, session_id: str, history: list[Message], tool_calls: list[ToolCall]
    ) -> None:
        r"""为本轮挂起授权 / 未及运行的工具调用补占位工具结果。"""
        await self._mark_awaiting_tool_results(session_id, history, tool_calls, _AWAITING_AUTHORIZATION_NOTE)

    async def _mark_awaiting_tool_results(
        self, session_id: str, history: list[Message], tool_calls: list[ToolCall], note: str
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
            ToolResultPart(tool_call_id=call.id, content=note, is_error=False)
            for call in tool_calls
            if call.id not in answered
        ]
        if not placeholders:
            return
        message = Message(role="tool", content=placeholders)
        history.append(message)
        await self.store.append(session_id, message)

    async def handle_card_action(self, event: Event) -> dict[str, Any]:
        r"""
        处理审批卡片的回传交互：**立即 ACK，后台执行**。

        从卡片回传值中读取 `__approval__` 与 `decision`。决策无效 / 无对应挂起审批时同步返回一个提示 `toast`，
        不消费审批（用户可重试）；**只有发起人本人可确认**（见下）。校验通过后，决定 / 执行 / 续跑主循环全部放到
        后台任务（`feishu.agent.loop.Agent._decide_and_resume`）进行，本方法**立即**返回一个「处理中」`toast`
        让飞书在超时前完成 ACK——即便被批准的工具很慢；待后台完成后再用 decided 卡片**原地 patch** 点击的卡片。

        最小权限：审批在创建时绑定到发起人（`owner_user_keys`）；此处校验点击者即发起人，且后台以发起人身份执行，
        从而群聊中他人无法替发起人确认或令写操作以他人身份 / 句柄执行。

        通常无需直接调用，而是经 [feishu.agent.registration.register_agent][] 注册为卡片回调事件的处理函数。

        Args:
            event: 飞书卡片回调事件，须具备 `.body` 属性。

        Returns:
            供飞书即时 ACK 的同步响应字典，通常为一个「处理中」`toast`（决定的最终结果由后台 patch 卡片体现，
            而非此返回值）。

        飞书文档:
            [卡片回传交互](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-callback-communication)

        Examples:
            >>> await agent.handle_card_action(event)  # doctest:+SKIP
            {'toast': {'type': 'info', 'content': 'processing…'}}
        """
        value = _action_value(event)
        approval_id = value.get("__approval__")
        if not approval_id:
            return {"toast": {"type": "info", "content": "no pending approval"}}
        # Validate the decision BEFORE touching the approval: a bogus/unrecognised
        # decision must neither resolve nor destroy the PendingApproval, so the
        # user can retry with a valid decision.
        decision = value.get("decision")
        if decision not in ("approve", "reject"):
            return {"toast": {"type": "info", "content": "invalid decision"}}
        approval = await self.approvals.get(approval_id)
        if approval is None:  # already resolved (e.g. a double click) or expired
            return {"toast": {"type": "info", "content": "no pending approval"}}
        # Least-privilege + fail-closed: only the IDENTIFIED initiator may confirm. An approval with no bound
        # owner (which _request_approval refuses to create) OR a clicker who isn't the initiator is rejected —
        # so in a group chat B cannot confirm A's write (which would otherwise run with B's identity / handles).
        clicker_keys = set(user_identity_keys(self._tool_context(event).requesting_user()))
        if not approval.owner_user_keys or not (clicker_keys & set(approval.owner_user_keys)):
            return {"toast": {"type": "error", "content": "this confirmation is not yours"}}
        # Decide + execute + resume entirely in the BACKGROUND so this card callback ACKs within Feishu's
        # timeout even when the approved tool is slow (file uploads, multi-step). The clicked card is then
        # patched in place with the decided card. The engine's tamper-check / claim / idempotent-replay
        # makes a double-click a safe no-op.
        from ..cards.callback import parse_action

        card_message_id = parse_action(event).message_id
        self._spawn_background(self._decide_and_resume(event, approval, decision, value, card_message_id))
        return {"toast": {"type": "info", "content": "processing…"}}

    async def resume_authorization(self, authorization_id: str, *, user: Mapping[str, Any] | None = None) -> str:
        r"""
        OAuth callback 成功保存用户 token 后，恢复一次挂起授权对应的原工具调用。

        产品侧应从已签名校验的 OAuth state 中取出 `authorization_id`，传入完成授权的 `user`，在保存 token 后
        调用本方法。返回值是机器可读状态：`"resumed"`、`"missing"`、`"forbidden"`、`"already_claimed"`
        等；面向用户的最终结果会发回原飞书会话，而不是依赖浏览器页面承载。
        """
        authorization = await self.authorizations.get(authorization_id)
        if authorization is None:
            return "missing"
        if user is None:
            await self._notify_authorization_resume_problem(
                authorization,
                "Authorization succeeded, but I could not verify who completed it. Please try again.",
            )
            return "forbidden"
        callback_keys = set(user_identity_keys(user))
        if not authorization.owner_user_keys or not (callback_keys & set(authorization.owner_user_keys)):
            return "forbidden"
        claim = await self.authorizations.claim(authorization_id)
        if claim is not ClaimResult.CLAIMED:
            if claim in (ClaimResult.EXPIRED, ClaimResult.MISSING):
                await self._notify_authorization_resume_problem(
                    authorization,
                    "Authorization succeeded, but the original request has expired. Please ask me again.",
                )
            return claim.value

        resume_event = self._event_from_pending_authorization(authorization)
        context = self._tool_context(resume_event)
        if authorization.owner_user_keys:
            context.user = user_from_identity_keys(authorization.owner_user_keys)
        with use_tool_context(context):
            try:
                progress = _ProgressCard(self, resume_event)
                await progress.step(
                    authorization.tool_name,
                    description=self._tool_description(authorization.tool_name),
                )
                try:
                    result = await self.registry.dispatch(authorization.tool_name, authorization.arguments)
                except Exception as exc:  # noqa: BLE001 - report tool failure to the model, not the browser
                    logging.getLogger("feishu").warning(
                        "authorization %s: tool %s raised %s during resume",
                        authorization.authorization_id,
                        authorization.tool_name,
                        type(exc).__name__,
                    )
                    result = ToolResult(
                        ToolOutcome.FAILED,
                        content=(
                            f"tool {authorization.tool_name} failed with {type(exc).__name__}; "
                            "see server logs for details"
                        ),
                        is_error=True,
                    )
                content, is_error, _ = _coerce_tool_result(result)
                result_part = ToolResultPart(
                    tool_call_id=authorization.tool_call_id,
                    content=content,
                    is_error=is_error,
                )
                async with self._session_lock(authorization.session_id):
                    history = await self.store.get(authorization.session_id)
                    if _replace_tool_result(history, authorization.tool_call_id, result_part):
                        await self.store.set(authorization.session_id, history)
                    else:
                        tool_msg = Message(role="tool", content=[result_part])
                        history.append(tool_msg)
                        await self.store.append(authorization.session_id, tool_msg)
                    suspension = await self._continue_tool_calls_after(
                        resume_event,
                        authorization.session_id,
                        history,
                        authorization.tool_call_id,
                        progress,
                    )
                    if suspension:
                        await progress.finalize(_suspension_progress_note(suspension))
                    else:
                        await self._loop(resume_event, authorization.session_id, history, progress=progress)
                await self.authorizations.complete(authorization_id, outcome="failed" if is_error else "executed")
                return "resumed"
            except Exception:  # noqa: BLE001 - callback background failures should be reported in chat and logged
                await self.authorizations.complete(authorization_id, outcome="frozen")
                logging.getLogger("feishu").exception(
                    "authorization %s: error resuming tool %s",
                    authorization.authorization_id,
                    authorization.tool_name,
                )
                try:
                    await self._finalize(
                        resume_event,
                        "Authorization succeeded, but I could not continue the original request. Please try again.",
                    )
                except Exception:  # noqa: BLE001 - nothing more to do; logs already have the underlying failure
                    logging.getLogger("feishu").debug("could not send authorization resume failure", exc_info=True)
                return "failed"

    async def _notify_authorization_resume_problem(self, authorization: PendingAuthorization, text: str) -> None:
        r"""Best-effort chat feedback for an OAuth callback that cannot resume a known pending authorization."""
        try:
            await self._finalize(self._event_from_pending_authorization(authorization), text)
        except Exception:  # noqa: BLE001 - callback path should not fail because chat feedback failed
            logging.getLogger("feishu").debug(
                "could not send authorization resume status for %s",
                authorization.authorization_id,
                exc_info=True,
            )

    def _event_from_pending_authorization(self, authorization: PendingAuthorization) -> Event:
        return Event.from_payload(
            {
                "header": {"event_type": "im.message.receive_v1"},
                "event": {
                    "message": {
                        "message_id": authorization.created_message_id,
                        "chat_id": authorization.chat_id,
                    }
                },
            }
        )

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
        r"""
        后台执行审批决定：`engine.on_decision`（含工具执行）→ 必要时续跑主循环 → 用 decided 卡片原地更新点击的卡片。

        放到后台是为了让卡片回调能在飞书超时前 ACK——即使被批准的工具很慢（上传文件、多步）。引擎自带防篡改 /
        幂等重放 / 并发认领，重复点击是无害的 no-op。任何异常只记录，并尽力把卡片更新为最终（decided）状态。

        执行身份**强制**为审批的发起人（`owner_user_keys`，而非点击者）：被批准的写操作以发起人身份执行，
        分享文件句柄也按发起人解析；handle_card_action 已校验点击者即发起人。
        """
        # Anchor the resumed turn to the ORIGINAL conversation: a real card.action.trigger carries
        # context.open_message_id/open_chat_id (no message{} node), so reusing the card event would lose the
        # message_id/chat_id and silently drop the final reply / auth / progress cards. Rebuild from the approval.
        resume_event = Event.from_payload(
            {
                "header": {"event_type": "im.message.receive_v1"},
                "event": {"message": {"message_id": approval.created_message_id, "chat_id": approval.chat_id}},
            }
        )
        context = self._tool_context(resume_event)
        if approval.owner_user_keys:
            context.user = user_from_identity_keys(approval.owner_user_keys)
        with use_tool_context(context):
            outcome = None
            try:
                progress = _ProgressCard(self, resume_event)
                if decision == "approve":
                    await progress.step(approval.tool_name, description=self._tool_description(approval.tool_name))
                outcome = await self.approval_engine.on_decision(
                    approval.approval_id,
                    decision,
                    expected_payload_sha256=value.get("payload_sha256"),
                    dispatch=self.registry.dispatch,
                )
                if _should_resume(outcome.status):
                    if outcome.auth_scopes:
                        call = ToolCall(
                            id=approval.tool_call_id,
                            name=approval.tool_name,
                            arguments=json.dumps(approval.arguments, ensure_ascii=False),
                        )
                        auth_result = ToolResult(
                            ToolOutcome.NEEDS_USER_AUTH,
                            content=outcome.content,
                            authorize_url=outcome.authorize_url,
                            auth_scopes=tuple(outcome.auth_scopes),
                            is_error=True,
                        )
                        if await self._request_authorization(resume_event, approval.session_id, [], call, auth_result):
                            result_part = ToolResultPart(
                                tool_call_id=approval.tool_call_id,
                                content=_AWAITING_AUTHORIZATION_NOTE,
                                is_error=False,
                            )
                            async with self._session_lock(approval.session_id):
                                history = await self.store.get(approval.session_id)
                                if _replace_tool_result(history, approval.tool_call_id, result_part):
                                    await self.store.set(approval.session_id, history)
                                else:
                                    tool_msg = Message(role="tool", content=[result_part])
                                    history.append(tool_msg)
                                    await self.store.append(approval.session_id, tool_msg)
                            await progress.finalize(_suspension_progress_note("authorization"))
                            return
                    content, tr_is_error, _ = _coerce_tool_result(outcome.content)
                    if outcome.authorize_url and not outcome.auth_scopes:
                        if await self._try_send_auth_card(resume_event, outcome.authorize_url):
                            content = _AUTH_CARD_SENT_NOTE
                        else:
                            content = f"{content}\nAuthorization URL: {outcome.authorize_url}".strip()
                    result_part = ToolResultPart(
                        tool_call_id=approval.tool_call_id,
                        content=content,
                        is_error=outcome.is_error or tr_is_error,
                    )
                    # Serialize the history read-modify-write against a concurrent message for the same session
                    # (same lock as run()), so the resumed turn can't be lost to / clobber a parallel turn.
                    async with self._session_lock(approval.session_id):
                        history = await self.store.get(approval.session_id)
                        # Replace the awaiting-confirmation placeholder written at suspension so there is exactly
                        # one result for this tool_call_id — even if the user clicked after abandoning the card and
                        # moving on. Only fall back to appending for histories that predate the placeholder.
                        if _replace_tool_result(history, approval.tool_call_id, result_part):
                            await self.store.set(approval.session_id, history)
                        else:
                            tool_msg = Message(role="tool", content=[result_part])
                            history.append(tool_msg)
                            await self.store.append(approval.session_id, tool_msg)
                        suspension = await self._continue_tool_calls_after(
                            resume_event, approval.session_id, history, approval.tool_call_id, progress
                        )
                        if suspension:
                            await progress.finalize(_suspension_progress_note(suspension))
                        else:
                            await self._loop(resume_event, approval.session_id, history, progress=progress)
            except (
                Exception
            ):  # noqa: BLE001 - a background decision/resume failure must not surface as an unhandled task error
                logging.getLogger("feishu").exception(
                    "handle_card_action: error deciding/resuming %s of %s (approval=%s)",
                    decision,
                    approval.tool_name,
                    approval.approval_id,
                )
            # Patch the clicked card with the decided card (best-effort; the result also arrives via reply).
            if card_message_id and self.client is not None and outcome is not None:
                try:
                    await self.client.im.patch(card_message_id, self._decided_card_builder(approval, decision, outcome))
                except Exception:  # noqa: BLE001
                    logging.getLogger("feishu").debug("could not patch the decided card", exc_info=True)

    def _default_approval_card(self, approval: PendingApproval) -> dict[str, Any]:
        from ..cards.builder import Card

        summary = _approval_arguments_summary(approval.arguments)
        return (
            Card()
            .header(f"Approve {approval.tool_name}?", template="orange")
            .markdown(f"Confirm **{approval.tool_name}**.\n\n{summary}")
            .button(
                "Approve",
                value={
                    "__approval__": approval.approval_id,
                    "decision": "approve",
                    "payload_sha256": approval.payload_sha256,
                },
                type="primary",
            )
            .button(
                "Reject",
                value={
                    "__approval__": approval.approval_id,
                    "decision": "reject",
                    "payload_sha256": approval.payload_sha256,
                },
                type="danger",
            )
            .to_dict()
        )

    def _default_decided_card(
        self, approval: PendingApproval, decision: str, outcome: ApprovalOutcome
    ) -> dict[str, Any]:
        from ..cards.builder import Card

        if outcome.status is ApprovalStatus.EXECUTED:
            template, verb = "green", "executed"
        elif outcome.status is ApprovalStatus.REJECTED:
            template, verb = "grey", "rejected"
        else:
            template, verb = "red", outcome.status.value
        return (
            Card()
            .header(f"{approval.tool_name} {verb}", template=template)
            .markdown(f"Action **{approval.tool_name}** was {verb}.")
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


def _accepts_positional_arguments(callback: Callable[..., Any], count: int) -> bool:
    r"""Return whether ``callback`` can be called with ``count`` positional arguments."""
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return True
    positional = 0
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            positional += 1
    return positional >= count


def _stringify(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


_AUTH_CARD_SENT_NOTE = (
    "user authorization required; an interactive authorization card with an authorize button was sent "
    "to the user. Briefly ask them to tap it to authorize, then you'll continue. Do NOT output any URL."
)

_AWAITING_APPROVAL_NOTE = "[Awaiting your confirmation — this action has not been performed yet.]"
_AWAITING_APPROVAL_PROGRESS_NOTE = AWAITING_APPROVAL_PROGRESS_TEXT
_AWAITING_AUTHORIZATION_NOTE = "[Awaiting user authorization — this tool has not been performed yet.]"
_AWAITING_AUTHORIZATION_PROGRESS_NOTE = AWAITING_AUTHORIZATION_PROGRESS_TEXT


def _suspension_progress_note(suspension: str) -> str:
    return _AWAITING_AUTHORIZATION_PROGRESS_NOTE if suspension == "authorization" else _AWAITING_APPROVAL_PROGRESS_NOTE


def _tool_calls_after(history: list[Message], tool_call_id: str) -> list[ToolCall]:
    r"""返回与 ``tool_call_id`` 位于同一条 assistant 消息中、且排在其后的工具调用。"""
    for message in history:
        if message.role != "assistant":
            continue
        after = False
        calls: list[ToolCall] = []
        for part in message.content:
            if not isinstance(part, ToolUsePart):
                continue
            if after:
                calls.append(
                    ToolCall(
                        id=part.id,
                        name=part.name,
                        arguments=json.dumps(part.arguments, ensure_ascii=False),
                    )
                )
            elif part.id == tool_call_id:
                after = True
        if after:
            return calls
    return []


def _replace_tool_result(history: list[Message], tool_call_id: str, new_part: ToolResultPart) -> bool:
    r"""把历史中匹配 `tool_call_id` 的工具结果原地替换为 `new_part`，命中返回 `True`、未命中返回 `False`。"""
    for message in history:
        if message.role != "tool":
            continue
        for index, part in enumerate(message.content):
            if isinstance(part, ToolResultPart) and part.tool_call_id == tool_call_id:
                message.content[index] = new_part
                return True
    return False


class _ProgressCard:
    r"""
    按轮进度卡片：循环开始时发送进度卡片，之后每步原地更新（`im.patch`），让用户看到「中间过程」。

    卡片样式由产品注入的 `progress_card_builder`（签名 `(tool_names, done, result_text) -> dict`）决定；未注入
    构造器、无客户端或事件缺少 `chat_id` 时全程空操作。进度 UI 出错绝不应影响主流程，故各处更新均吞掉异常。
    """

    def __init__(self, agent: Agent, event: Event) -> None:
        self._agent = agent
        message = event.body.get("message") or {}
        self._chat_id = message.get("chat_id")
        self._message_id: str | None = None
        self._steps: list[str] = []
        self._started_at = time.monotonic()
        self._last_summary_at = 0.0
        self._last_status_text = ""
        self._done = False

    def _ready(self) -> tuple[Callable[[list[str], bool, str], dict[str, Any]], FeishuClient, str] | None:
        r"""返回 `(builder, client, chat_id)`（均非空）表示本轮可渲染进度卡片；缺构造器 / 客户端 / chat_id 时返回 `None`。"""
        builder = self._agent._progress_card_builder
        client = self._agent.client
        chat_id = self._chat_id
        if builder is None or client is None or not chat_id:
            return None
        return builder, client, chat_id

    async def start(self) -> None:
        r"""发送初始进度卡片，让普通文本回复也能即时给用户一个可见状态。"""
        ready = self._ready()
        if ready is None or self._message_id is not None:
            return
        builder, client, chat_id = ready
        card = builder([], False, "")
        try:
            resp = await client.im.send(chat_id, card, msg_type="interactive", receive_id_type="chat_id")
            self._message_id = _message_id_from_response(resp)
        except Exception:  # noqa: BLE001 - the progress UI must never break the turn
            logging.getLogger("feishu").debug("progress card start failed", exc_info=True)

    async def thinking(self, *, reasoning: str, text: str = "") -> None:
        r"""把近期模型 reasoning 交给产品侧摘要器，产出一句可见进度并原地更新卡片。"""
        summarizer = self._agent._progress_summarizer
        if summarizer is None:
            return
        now = time.monotonic()
        if now - self._started_at < self._agent._progress_summary_delay_seconds:
            return
        interval = self._agent._progress_summary_interval_seconds
        if self._last_summary_at and now - self._last_summary_at < interval:
            return
        max_chars = self._agent._progress_reasoning_max_chars
        snapshot = ProgressSnapshot(
            phase="thinking",
            reasoning=reasoning[-max_chars:] if max_chars else "",
            text=text[-1000:],
            tool_name=self._steps[-1] if self._steps else None,
            elapsed_seconds=now - self._started_at,
        )
        self._last_summary_at = now
        try:
            status = await self._summarize(snapshot)
        except Exception:  # noqa: BLE001 - progress summaries must never break the main turn
            logging.getLogger("feishu").debug("progress summarizer failed", exc_info=True)
            return
        if isinstance(status, str):
            await self.update_status(status)

    async def _summarize(self, snapshot: ProgressSnapshot) -> str | None:
        summarizer = self._agent._progress_summarizer
        if summarizer is None:
            return None
        status = summarizer(snapshot)
        if inspect.isawaitable(status):
            status = await status
        return status if isinstance(status, str) else None

    async def _summarize_and_update(self, snapshot: ProgressSnapshot) -> None:
        try:
            status = await self._summarize(snapshot)
        except Exception:  # noqa: BLE001 - progress summaries must never break the main turn
            logging.getLogger("feishu").debug("progress summarizer failed", exc_info=True)
            return
        if status:
            await self.update_status(status)

    async def update_status(self, status_text: str) -> bool:
        r"""用一条短状态文案原地更新进度卡；无卡片时尝试发送首张。"""
        status_text = status_text.strip()
        if self._done or not status_text or status_text == self._last_status_text:
            return False
        ready = self._ready()
        if ready is None:
            return False
        builder, client, chat_id = ready
        card = builder(list(self._steps), False, status_text)
        try:
            if self._message_id is None:
                resp = await client.im.send(chat_id, card, msg_type="interactive", receive_id_type="chat_id")
                self._message_id = _message_id_from_response(resp)
            else:
                await client.im.patch(self._message_id, card)
            self._last_status_text = status_text
            return True
        except Exception:  # noqa: BLE001 - progress UI must never break the turn
            logging.getLogger("feishu").debug("progress status update failed", exc_info=True)
            return False

    async def step(self, tool_name: str, *, description: str | None = None) -> None:
        r"""记录一步并发送 / 更新进度卡片（首步发送，后续 patch）。"""
        ready = self._ready()
        if ready is None:
            return
        builder, client, chat_id = ready
        self._steps.append(tool_name)
        card = builder(list(self._steps), False, "")
        try:
            if self._message_id is None:
                resp = await client.im.send(chat_id, card, msg_type="interactive", receive_id_type="chat_id")
                self._message_id = _message_id_from_response(resp)
            else:
                await client.im.patch(self._message_id, card)
        except Exception:  # noqa: BLE001 - the progress UI must never break the turn
            logging.getLogger("feishu").debug("progress card update failed", exc_info=True)
        if self._agent._progress_summarizer is not None:
            self._agent._spawn_background(
                self._summarize_and_update(
                    ProgressSnapshot(
                        phase="tool",
                        tool_name=tool_name,
                        tool_description=description,
                        elapsed_seconds=time.monotonic() - self._started_at,
                    )
                )
            )

    async def finalize(self, result_text: str) -> bool:
        r"""
        收尾：若此前已发过进度卡片，则把它**原地替换**为最终答案（`im.patch`），并返回 `True` 表示已回复。

        返回 `False` 表示本轮没有进度卡片（未调用任何工具），调用方应改用常规文本回复。
        """
        ready = self._ready()
        if ready is None or self._message_id is None:
            return False
        builder, client, _ = ready
        self._done = True
        try:
            card = builder(list(self._steps), True, result_text)
            await client.im.patch(self._message_id, card)
            return True
        except Exception:  # noqa: BLE001 - on failure, fall back to a normal reply
            logging.getLogger("feishu").debug("progress card finalize failed", exc_info=True)
            return False


def _message_id_from_response(response: Any) -> str | None:
    r"""从飞书 send/patch 响应里兼容抽取消息 ID。"""
    if not hasattr(response, "get"):
        return None
    message_id = response.get("message_id")
    if message_id:
        return str(message_id)
    data = response.get("data")
    if hasattr(data, "get") and data.get("message_id"):
        return str(data.get("message_id"))
    return None


def _collect_shared_file_ids(value: Any) -> list[str]:
    # Walk tool arguments for shared-file handles (the 'sf_' prefix) anywhere — scalar, list, or dict value.
    out: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if node.startswith("sf_"):
                out.append(node)
        elif isinstance(node, dict):
            for item in node.values():
                walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(value)
    return out


def _shared_files_note(shared: list[Any]) -> str:
    # Neutral note (no product copy): tells the model which files are available BY HANDLE — never bytes.
    items = "; ".join(f"{sf.file_id} (name={sf.name!r}, type={sf.kind})" for sf in shared)
    return (
        f"[The user shared {len(shared)} file(s), referenceable by file_id: {items}. "
        f"To act on a file, call a tool that accepts a file_id; you cannot see the raw bytes.]"
    )


def _approval_arguments_summary(arguments: dict[str, Any]) -> str:
    if not arguments:
        return "No arguments."
    lines = []
    for key in sorted(arguments):
        lines.append(f"- `{key}`: {_approval_argument_label(key, arguments[key])}")
    return "\n".join(lines)


def _approval_argument_label(key: str, value: Any, *, depth: int = 0, sensitive: bool = False) -> str:
    sensitive = sensitive or _approval_argument_key_is_sensitive(key)
    if isinstance(value, dict):
        if sensitive or depth >= 2:
            return f"object with {len(value)} field(s)"
        parts = []
        for child_key in sorted(value)[:5]:
            child = _approval_argument_label(str(child_key), value[child_key], depth=depth + 1, sensitive=sensitive)
            parts.append(f"`{child_key}`: {child}")
        suffix = ", ..." if len(value) > 5 else ""
        return "{" + ", ".join(parts) + suffix + "}"
    if isinstance(value, list):
        if sensitive or depth >= 1:
            return f"list with {len(value)} item(s)"
        parts = [_approval_argument_label(key, item, depth=depth + 1, sensitive=sensitive) for item in value[:3]]
        suffix = ", ..." if len(value) > 3 else ""
        return "[" + ", ".join(parts) + suffix + "]"
    if isinstance(value, str):
        if sensitive or value.startswith(("sf_", "pa_")):
            return "handle" if value else "empty text"
        return f"text ({len(value)} chars)"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float):
        return str(value)
    return type(value).__name__


def _approval_argument_key_is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(
        part in lowered
        for part in (
            "account",
            "attachment",
            "bank",
            "email",
            "file",
            "image",
            "key",
            "message_id",
            "mobile",
            "open_id",
            "password",
            "phone",
            "secret",
            "token",
            "union_id",
            "user_id",
        )
    )


def _coerce_tool_result(result: Any) -> tuple[str, bool, ToolResult | None]:
    # Normalize a tool handler's return into (content_text, is_error, tool_result).
    # Handlers may return a structured ToolResult or any raw JSON-able value. A non-success
    # outcome (NEEDS_USER_AUTH, BLOCKED, FAILED, CANCELLED) surfaces as an error
    # tool message so the model knows the call did not succeed.
    if isinstance(result, ToolResult):
        is_error = result.is_error or result.outcome not in (ToolOutcome.COMPLETED, ToolOutcome.INFORMATIONAL)
        text = _stringify(result.content) if result.content is not None else ""
        if result.authorize_url:
            text = f"{text}\nAuthorization URL: {result.authorize_url}".strip()
        return text, is_error, result
    if result is None:
        return "", False, None
    return _stringify(result), False, None


def _should_resume(status: ApprovalStatus) -> bool:
    # Resume the model turn on a terminal user decision that warrants a fresh reply:
    # a successful execution, an idempotent replay, an explicit rejection, a tool-reported
    # failure, or a frozen unknown result. Replays/frozen results still need to replace the
    # suspended placeholder so future model turns see truthful tool history.
    return status in (
        ApprovalStatus.EXECUTED,
        ApprovalStatus.REPLAYED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.FAILED,
        ApprovalStatus.FROZEN,
    )


def _outcome_toast(outcome: ApprovalOutcome) -> dict[str, str]:
    if outcome.status is ApprovalStatus.EXECUTED:
        return {"type": "success", "content": "Approved"}
    if outcome.status is ApprovalStatus.REJECTED:
        return {"type": "info", "content": "Rejected"}
    if outcome.status is ApprovalStatus.REPLAYED:
        return {"type": "info", "content": "Already done"}
    return {"type": "error", "content": outcome.status.value}


def _default_compact_reply(before: int, after: int) -> str:
    r"""压缩命令的中性默认回执；产品可注入本地化版本。"""
    if after < before:
        return f"Compacted {before} messages into {after}."
    return "Nothing to compact yet."


def _message_text(message: Message) -> str:
    r"""拼接一条消息里的所有文本片段（用于 /clear 等命令检测）。"""
    parts = [
        part.text
        for part in (getattr(message, "content", None) or [])
        if isinstance(getattr(part, "text", None), str) and part.text
    ]
    return "\n".join(parts).strip()


def _part_chars(part: Any) -> int:
    r"""估算单个内容片段的字符数：文本、工具调用参数、工具结果都计入。"""
    total = 0
    for attr in ("text", "content", "arguments"):
        value = getattr(part, attr, None)
        if isinstance(value, str):
            total += len(value)
        elif value is not None:
            total += len(repr(value))
    return total


def _estimate_tokens(messages: list[Message]) -> int:
    r"""粗略估算历史 token 量（~4 字符/token + 每条少量开销），仅用于触发摘要阈值，无需精确分词。"""
    return sum(sum(_part_chars(part) for part in (getattr(m, "content", None) or [])) // 4 + 4 for m in messages)


def _render_messages_for_summary(messages: list[Message]) -> str:
    r"""把历史消息渲染为可读纯文本，供摘要模型阅读；工具调用 / 结果做紧凑表示并截断。"""
    lines: list[str] = []
    for message in messages:
        role = getattr(message, "role", "?")
        chunks: list[str] = []
        for part in getattr(message, "content", None) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                chunks.append(text)
                continue
            name = getattr(part, "name", None)
            if name:
                chunks.append(f"[tool call: {name}]")
                continue
            content = getattr(part, "content", None)
            if content is not None:
                chunks.append(f"[tool result: {str(content)[:500]}]")
        if chunks:
            lines.append(f"{role}: {' '.join(chunks)}")
    return "\n".join(lines)
