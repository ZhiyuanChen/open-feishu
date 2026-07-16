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

import inspect
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("feishu")

PROGRESS_SUMMARY_INSTRUCTION = (
    "You are an internal progress-status summarizer for a Feishu agent. Rewrite the current work state into one "
    "short user-visible Chinese status sentence. Do not expose chain-of-thought. Do not mention elapsed time, "
    "seconds, or wait duration. Do not output tool arguments, URLs, tokens, IDs, phone numbers, emails, or secrets. "
    "Return only one sentence, with no Markdown or explanation."
)

_ELAPSED_TIME_RE = re.compile(
    r"(?:已经|已)?(?:处理|思考|分析|运行|执行|等待|耗时)(?:了)?\s*\d+(?:\.\d+)?\s*"
    r"(?:秒|s|sec|secs|second|seconds)[,，。；;、\s]*",
    re.IGNORECASE,
)


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


class _ProgressCard:
    r"""
    按轮进度卡片：循环开始时发送进度卡片，之后每步原地更新（`im.patch`），让用户看到「中间过程」。

    卡片样式由产品注入的 `progress_card_builder`（签名 `(tool_names, done, result_text) -> dict`）决定；未注入
    构造器、无客户端或事件缺少 `chat_id` 时全程空操作。进度 UI 出错绝不应影响主流程，故各处更新均吞掉异常。
    """

    def __init__(self, agent: Any, event: Any) -> None:
        self._agent = agent
        message = event.body.get("message") or {}
        self._chat_id = message.get("chat_id")
        self._message_id: str | None = None
        self._steps: list[str] = []
        self._started_at = time.monotonic()
        self._last_summary_at = 0.0
        self._last_status_text = ""
        self._done = False

    @property
    def message_id(self) -> str | None:
        r"""已发送进度卡片的飞书 message id；尚未发送时为 `None`。"""
        return self._message_id

    def reuse(self, message_id: str | None) -> None:
        r"""复用已存在的进度卡片，使恢复后的工作继续原地 patch。"""
        if message_id and self._message_id is None:
            self._message_id = message_id

    def _ready(self) -> tuple[Callable[[list[str], bool, str], dict[str, Any]], Any, str] | None:
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
            logger.debug("progress card start failed", exc_info=True)

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
            logger.debug("progress summarizer failed", exc_info=True)
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
            logger.debug("progress summarizer failed", exc_info=True)
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
            logger.debug("progress status update failed", exc_info=True)
            return False

    async def replace_with_card(self, card: dict[str, Any]) -> str | None:
        r"""把已存在的进度卡原地替换为外部卡片，并阻止后续 finalize 覆盖它。"""
        ready = self._ready()
        if ready is None or self._message_id is None or self._done:
            return None
        _, client, _ = ready
        try:
            await client.im.patch(self._message_id, card)
            self._done = True
            return self._message_id
        except Exception:  # noqa: BLE001 - caller can fall back to sending a separate card
            logger.debug("progress card replacement failed", exc_info=True)
            return None

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
            logger.debug("progress card update failed", exc_info=True)
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
        if self._done:
            return True
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
            logger.debug("progress card finalize failed", exc_info=True)
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


def _pending_progress_extra(progress: _ProgressCard | None) -> dict[str, Any]:
    message_id = progress.message_id if progress is not None else None
    return {"progress_message_id": message_id} if message_id else {}


def _progress_message_id(extra: Mapping[str, Any] | None) -> str | None:
    if not extra:
        return None
    message_id = extra.get("progress_message_id")
    return str(message_id) if message_id else None


def build_progress_summarizer(
    backend: Any,
    *,
    timeout_seconds: float = 1.2,
    max_chars: int = 60,
    instruction: str = PROGRESS_SUMMARY_INSTRUCTION,
) -> Any:
    r"""基于通用文本摘要器构建一次性的进度文案摘要器。"""
    from .summarization import TextSummaryRequest, build_fast_text_summarizer

    text_summarizer = build_fast_text_summarizer(
        backend,
        timeout_seconds=timeout_seconds,
        default_max_chars=max_chars,
    )

    async def summarize(snapshot: ProgressSnapshot) -> str | None:
        prompt = _prompt(snapshot, max_chars=max_chars)
        result = await text_summarizer(
            TextSummaryRequest(
                kind="progress",
                instruction=instruction,
                text=prompt,
                max_chars=max_chars,
            )
        )
        status = _clean_status(result or "", max_chars=max_chars)
        if status is None:
            logger.info("progress summarizer returned empty text; using fallback")
            return _fallback_status(snapshot, max_chars=max_chars)
        return status

    return summarize


def _prompt(snapshot: ProgressSnapshot, *, max_chars: int) -> str:
    reasoning = snapshot.reasoning[-4000:]
    visible_text = snapshot.text[-600:]
    tool = snapshot.tool_name or "none"
    description = snapshot.tool_description or ""
    phase = {"thinking": "思考中", "tool": "调用能力中", "final": "收尾中"}.get(snapshot.phase, snapshot.phase)
    details = reasoning or visible_text or description or tool
    return (
        f"请把下面的处理进展改写成一句用户可见的中文进度文案，不超过 {max_chars} 个字。\n"
        f"当前阶段：{phase}\n"
        f"正在使用的能力：{tool}\n"
        f"能力说明：{description}\n"
        f"进展信息：{details}"
    )


def _clean_status(text: str, *, max_chars: int) -> str | None:
    status = " ".join(text.replace("\n", " ").split()).strip(" `\"'")
    if not status:
        return None
    status = _redact(status)
    status = _strip_elapsed_time(status)
    if len(status) > max_chars:
        status = status[: max(0, max_chars - 1)].rstrip() + "…"
    return status or None


def _redact(text: str) -> str:
    text = re.sub(r"https?://\S+", "[链接]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", "[邮箱]", text)
    text = re.sub(r"\b1[3-9]\d{9}\b", "[手机号]", text)
    text = re.sub(r"\b(?:ou|oc|om|on|cli|user|union|open)_[A-Za-z0-9_-]{6,}\b", "[标识]", text)
    text = re.sub(r"\b[A-Fa-f0-9]{24,}\b", "[标识]", text)
    return text


def _strip_elapsed_time(text: str) -> str:
    return _ELAPSED_TIME_RE.sub("", text).strip(" ，,；;、")


def _fallback_status(snapshot: ProgressSnapshot, *, max_chars: int) -> str:
    if snapshot.phase == "tool":
        if snapshot.tool_description:
            status = snapshot.tool_description.strip("。.! ")
        elif snapshot.tool_name:
            status = f"正在调用 {snapshot.tool_name}"
        else:
            status = "正在调用能力"
    else:
        status = "正在处理你的请求"
    return _clean_status(status, max_chars=max_chars) or "正在处理你的请求"
