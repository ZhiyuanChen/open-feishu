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
from typing import Any, AsyncIterator, Sequence

from ..llm import (
    Message,
    MessageStop,
    ReasoningDelta,
    StopReason,
    StreamChunk,
    TextDelta,
    TextPart,
    ToolCallDelta,
    ToolResultPart,
    ToolSpec,
    ToolUsePart,
)

# NOTE: OpenAI Responses API streaming event names are unconfirmed (verify-before-code);
# this adapter implements the confirmed Chat Completions streaming flavor only.

_FINISH_MAP = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "function_call": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
    "content_filter": StopReason.REFUSAL,
}


def _map_finish_reason(raw: str | None) -> StopReason:
    return _FINISH_MAP.get(raw or "", StopReason.OTHER)


def _to_openai_tools(tools: Sequence[ToolSpec]) -> list[dict]:
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
        for t in tools
    ]


def _to_openai_messages(messages: Sequence[Message], system: str | None) -> list[dict]:
    out: list[dict] = []
    if system is not None:
        out.append({"role": "system", "content": system})
    for msg in messages:
        if msg.role == "tool":
            for p in msg.content:
                if isinstance(p, ToolResultPart):
                    out.append({"role": "tool", "tool_call_id": p.tool_call_id, "content": p.content})
            continue
        text_parts = [p.text for p in msg.content if isinstance(p, TextPart)]
        tool_uses = [p for p in msg.content if isinstance(p, ToolUsePart)]
        if msg.role == "assistant" and tool_uses:
            out.append(
                {
                    "role": "assistant",
                    "content": "".join(text_parts) or None,
                    "tool_calls": [
                        {
                            "id": p.id,
                            "type": "function",
                            "function": {"name": p.name, "arguments": json.dumps(p.arguments)},
                        }
                        for p in tool_uses
                    ],
                }
            )
        else:
            out.append({"role": msg.role, "content": "".join(text_parts)})
    return out


async def _translate_chunks(chunks: AsyncIterator[Any]) -> AsyncIterator[StreamChunk]:
    stop_reason = StopReason.OTHER
    usage: dict[str, int] | None = None
    async for chunk in chunks:
        u = getattr(chunk, "usage", None)
        if u is not None:
            usage = {
                k: getattr(u, k)
                for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                if getattr(u, k, None) is not None
            }
            # Prompt-cache hit count (OpenAI/qwen put it under prompt_tokens_details.cached_tokens) — lets the
            # caller track cache hit-rate, which dominates cost for long sessions.
            details = getattr(u, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", None) if details is not None else None
            if cached is not None:
                usage["cached_tokens"] = cached
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        choice = choices[0]
        delta = getattr(choice, "delta", None)
        if delta is not None:
            reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if reasoning:
                yield ReasoningDelta(text=reasoning)
            if getattr(delta, "content", None):
                yield TextDelta(text=delta.content)
            for tc in getattr(delta, "tool_calls", None) or []:
                func = getattr(tc, "function", None)
                yield ToolCallDelta(
                    index=tc.index,
                    id=getattr(tc, "id", None),
                    name=getattr(func, "name", None) if func else None,
                    arguments=(getattr(func, "arguments", None) or "") if func else "",
                )
        if getattr(choice, "finish_reason", None) is not None:
            # Record the stop reason but keep consuming: with stream_options include_usage, OpenAI sends a
            # trailing usage-only chunk AFTER the finish_reason chunk, so stopping here would drop usage.
            stop_reason = _map_finish_reason(choice.finish_reason)
    yield MessageStop(stop_reason=stop_reason, usage=usage)


class OpenAIBackend:
    r"""
    OpenAI Chat Completions API 的 [feishu.agent.llm.LlmBackend][] 实现。

    将与厂商无关的 [feishu.agent.llm.Message][]、[feishu.agent.llm.ToolSpec][] 翻译为 Chat Completions
    请求格式（系统提示词作为 `role="system"` 的消息），并把其流式分片归一化为
    [feishu.agent.llm.StreamChunk][]。`openai` SDK 仅在未注入 `client` 时按需懒加载，核心模块本身不依赖
    该 SDK。本适配器仅实现已确认的 Chat Completions 流式协议。

    Args:
        client: 已构造的 `openai.AsyncOpenAI` 客户端。为 `None` 时自动创建一个。
        model: 模型名称，例如 `gpt-4o`。
        **defaults: 透传给 OpenAI API 的默认参数，例如 `temperature`。

    Examples:
        >>> backend = OpenAIBackend(model="gpt-4o", temperature=0.7)  # doctest:+SKIP
    """

    def __init__(self, client: Any = None, *, model: str, **defaults: Any):
        if client is None:
            import openai  # imported lazily; core never imports the SDK

            client = openai.AsyncOpenAI()
        self._client = client
        self._model = model
        self._defaults = defaults

    def stream(
        self,
        *,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        r"""
        调用 OpenAI Chat Completions API 流式生成一轮响应。

        Args:
            messages: 截至当前轮次的对话历史。
            tools: 本轮可供模型调用的工具声明。
            system: 系统提示词，将作为首条 `role="system"` 消息加入请求。
            **kwargs: 覆盖默认参数（如 `model`）或追加的额外参数。

        Returns:
            逐个产出归一化 [feishu.agent.llm.StreamChunk][] 的异步迭代器。

        飞书文档:
            [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat)

        Examples:
            >>> async for chunk in backend.stream(messages=history):  # doctest:+SKIP
            ...     print(chunk)
            TextDelta(text='你好')
            MessageStop(stop_reason=<StopReason.END_TURN: 'end_turn'>, usage={'prompt_tokens': 8})
        """
        params: dict[str, Any] = {
            "model": kwargs.pop("model", self._model),
            "messages": _to_openai_messages(messages, system),
            "stream": True,
            "stream_options": {"include_usage": True},
            **self._defaults,
            **kwargs,
        }
        if tools:
            params["tools"] = _to_openai_tools(tools)

        async def _gen() -> AsyncIterator[StreamChunk]:
            stream = await self._client.chat.completions.create(**params)
            async for chunk in _translate_chunks(stream):
                yield chunk

        return _gen()
