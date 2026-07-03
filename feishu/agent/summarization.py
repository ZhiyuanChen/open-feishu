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
import logging
from dataclasses import dataclass
from typing import Any

from .llm import Message, TextPart

logger = logging.getLogger("feishu")

TEXT_SUMMARY_SYSTEM = (
    "You are an internal text summarizer. You only receive content that has already been extracted as text; "
    "do not claim to read raw images, audio, PDFs, or other non-text inputs. Preserve facts, tasks, dates, "
    "risks, and decisions from the provided text. Do not output tool arguments, tokens, secrets, or unrelated IDs."
)


@dataclass(frozen=True)
class TextSummaryRequest:
    r"""发给快速辅助模型的纯文本摘要请求。"""

    kind: str
    instruction: str
    text: str
    max_chars: int | None = None
    language: str = "zh-CN"


def build_fast_text_summarizer(
    backend: Any,
    *,
    timeout_seconds: float = 8.0,
    default_max_chars: int = 1200,
    system: str = TEXT_SUMMARY_SYSTEM,
) -> Any:
    r"""基于快速 LLM backend 构建可复用的纯文本摘要器。"""

    async def summarize(request: TextSummaryRequest) -> str | None:
        if not request.text.strip():
            return None
        from .loop import accumulate_stream

        max_chars = max(1, request.max_chars or default_max_chars)
        prompt = _prompt(request, max_chars=max_chars)
        try:
            result = await asyncio.wait_for(
                accumulate_stream(
                    backend.stream(
                        messages=[Message(role="user", content=[TextPart(text=prompt)])],
                        tools=(),
                        system=system,
                    )
                ),
                timeout=max(0.1, timeout_seconds),
            )
        except TimeoutError:
            logger.info("fast text summarizer timed out after %.1fs", max(0.1, timeout_seconds))
            return None
        except Exception as exc:
            logger.info("fast text summarizer failed with %s", type(exc).__name__)
            return None
        return _clean_summary(result.text or result.reasoning, max_chars=max_chars)

    return summarize


async def maybe_summarize(agent: Any, session_id: str, history: list[Message]) -> list[Message]:
    r"""
    历史超过 token 阈值时自动压缩，使长会话维持稳定、可被前缀缓存命中的 prefix。

    未配置阈值（0）或未超阈值时原样返回。
    """
    threshold = agent._summarize_threshold_tokens
    if not threshold or estimate_tokens(history) <= threshold:
        return history
    return await summarize_history(agent, session_id, history)


async def summarize_history(agent: Any, session_id: str, history: list[Message]) -> list[Message]:
    r"""把较早轮次压缩为一条摘要消息、保留最近 N 条原样，并持久化压缩后的历史。"""
    keep = max(0, agent._summarize_keep_recent)
    old = history[:-keep] if keep else list(history)
    recent = history[-keep:] if keep else []
    if not old:
        return history
    try:
        summary = await summarize_messages(agent, old)
    except Exception:  # noqa: BLE001 - summarization must never break the turn
        logger.exception("history summarization failed; keeping full history")
        return history
    if not summary.strip():
        return history
    summary_msg = Message(role="user", content=[TextPart(text=f"{agent._summary_prefix}\n{summary.strip()}")])
    compacted = [summary_msg, *recent]
    await agent.store.set(session_id, compacted)
    logger.info("compacted %d old messages into 1 (history %d -> %d messages)", len(old), len(history), len(compacted))
    return compacted


async def summarize_messages(agent: Any, messages: list[Message]) -> str:
    r"""用注入的 `summarizer` 生成摘要，或回退到用 Agent backend 生成。"""
    if agent._summarizer is not None:
        return await agent._summarizer(messages)
    from .loop import accumulate_stream

    convo = render_messages_for_summary(messages)
    prompt = [Message(role="user", content=[TextPart(text=f"{agent._summary_instruction}\n\n{convo}")])]
    result = await accumulate_stream(
        agent.backend.stream(messages=prompt, tools=(), system=None, **agent.backend_kwargs)
    )
    return result.text


def default_compact_reply(before: int, after: int) -> str:
    r"""压缩命令的中性默认回执；产品可注入本地化版本。"""
    if after < before:
        return f"Compacted {before} messages into {after}."
    return "Nothing to compact yet."


def estimate_tokens(messages: list[Message]) -> int:
    r"""粗略估算历史 token 量（~4 字符/token + 每条少量开销），仅用于触发摘要阈值。"""
    return sum(sum(_part_chars(part) for part in (getattr(m, "content", None) or [])) // 4 + 4 for m in messages)


def render_messages_for_summary(messages: list[Message]) -> str:
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


def _prompt(request: TextSummaryRequest, *, max_chars: int) -> str:
    return (
        f"Task kind: {request.kind}\n"
        f"Output language: {request.language}\n"
        f"Maximum output length: {max_chars} characters\n"
        f"Instruction:\n{request.instruction.strip()}\n\n"
        f"Input text:\n{request.text.strip()}"
    )


def _clean_summary(text: str, *, max_chars: int) -> str | None:
    summary = " ".join(text.replace("\n", " ").split()).strip(" `\"'")
    if not summary:
        return None
    if len(summary) > max_chars:
        summary = summary[: max(0, max_chars - 1)].rstrip() + "…"
    return summary or None


def _part_chars(part: Any) -> int:
    total = 0
    for attr in ("text", "content", "arguments"):
        value = getattr(part, attr, None)
        if isinstance(value, str):
            total += len(value)
        elif value is not None:
            total += len(repr(value))
    return total
