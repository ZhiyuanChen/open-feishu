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

from typing import Any, AsyncIterator, Sequence

from ..llm import (
    Message,
    MessageStop,
    StopReason,
    StreamChunk,
    TextDelta,
    TextPart,
    ToolCallDelta,
    ToolResultPart,
    ToolSpec,
    ToolUsePart,
)

_STOP_MAP = {
    "end_turn": StopReason.END_TURN,
    "stop_sequence": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "refusal": StopReason.REFUSAL,
}


def _map_stop_reason(raw: str | None) -> StopReason:
    return _STOP_MAP.get(raw or "", StopReason.OTHER)


def _to_anthropic_tools(tools: Sequence[ToolSpec]) -> list[dict]:
    return [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]


def _to_anthropic_messages(messages: Sequence[Message]) -> list[dict]:
    out: list[dict] = []
    for msg in messages:
        if msg.role == "tool":
            blocks = [
                {
                    "type": "tool_result",
                    "tool_use_id": p.tool_call_id,
                    "content": p.content,
                    "is_error": p.is_error,
                }
                for p in msg.content
                if isinstance(p, ToolResultPart)
            ]
            out.append({"role": "user", "content": blocks})
            continue
        blocks = []
        for p in msg.content:
            if isinstance(p, TextPart):
                blocks.append({"type": "text", "text": p.text})
            elif isinstance(p, ToolUsePart):
                blocks.append({"type": "tool_use", "id": p.id, "name": p.name, "input": p.arguments})
        out.append({"role": msg.role, "content": blocks})
    return out


async def _translate_events(events: AsyncIterator[Any]) -> AsyncIterator[StreamChunk]:
    stop_reason = StopReason.OTHER
    usage: dict[str, int] | None = None
    async for event in events:
        kind = getattr(event, "type", None)
        if kind == "content_block_start":
            block = event.content_block
            if getattr(block, "type", None) == "tool_use":
                yield ToolCallDelta(index=event.index, id=block.id, name=block.name, arguments="")
        elif kind == "content_block_delta":
            delta = event.delta
            dtype = getattr(delta, "type", None)
            if dtype == "text_delta":
                yield TextDelta(text=delta.text)
            elif dtype == "input_json_delta":
                yield ToolCallDelta(index=event.index, arguments=delta.partial_json)
        elif kind == "message_delta":
            stop_reason = _map_stop_reason(getattr(event.delta, "stop_reason", None))
            usage_obj = getattr(event, "usage", None)
            if usage_obj is not None and getattr(usage_obj, "output_tokens", None) is not None:
                usage = {"output_tokens": usage_obj.output_tokens}
        elif kind == "message_stop":
            yield MessageStop(stop_reason=stop_reason, usage=usage)


class AnthropicBackend:
    r"""
    Anthropic Messages API 的 [feishu.agent.llm.LlmBackend][] 实现。

    将与厂商无关的 [feishu.agent.llm.Message][]、[feishu.agent.llm.ToolSpec][] 翻译为 Anthropic 请求格式，
    并把其流式事件归一化为 [feishu.agent.llm.StreamChunk][]。`anthropic` SDK 仅在未注入 `client` 时按需
    懒加载，核心模块本身不依赖该 SDK。

    Args:
        client: 已构造的 `anthropic.AsyncAnthropic` 客户端。为 `None` 时自动创建一个。
        model: 模型名称，例如 `claude-sonnet-4-5`。
        **defaults: 透传给 Anthropic API 的默认参数，例如 `max_tokens`、`temperature`。

    Examples:
        >>> backend = AnthropicBackend(model="claude-sonnet-4-5", max_tokens=1024)  # doctest:+SKIP
    """

    def __init__(self, client: Any = None, *, model: str, **defaults: Any):
        if client is None:
            import anthropic  # imported lazily; core never imports the SDK

            client = anthropic.AsyncAnthropic()
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
        调用 Anthropic Messages API 流式生成一轮响应。

        Args:
            messages: 截至当前轮次的对话历史。
            tools: 本轮可供模型调用的工具声明。
            system: 系统提示词，作为顶层 `system` 参数传入。
            **kwargs: 覆盖默认参数（如 `model`、`max_tokens`）或追加的额外参数。

        Returns:
            逐个产出归一化 [feishu.agent.llm.StreamChunk][] 的异步迭代器。

        参考文档:
            [Anthropic Messages API](https://docs.anthropic.com/en/api/messages)

        Examples:
            >>> async for chunk in backend.stream(messages=history):  # doctest:+SKIP
            ...     print(chunk)
            TextDelta(text='你好')
            MessageStop(stop_reason=<StopReason.END_TURN: 'end_turn'>, usage={'output_tokens': 5})
        """
        params: dict[str, Any] = {
            "model": kwargs.pop("model", self._model),
            "max_tokens": kwargs.pop("max_tokens", self._defaults.get("max_tokens", 1024)),
            "messages": _to_anthropic_messages(messages),
            **{k: v for k, v in self._defaults.items() if k != "max_tokens"},
            **kwargs,
        }
        if tools:
            params["tools"] = _to_anthropic_tools(tools)
        if system is not None:
            params["system"] = system

        async def _gen() -> AsyncIterator[StreamChunk]:
            async with self._client.messages.stream(**params) as stream:
                async for chunk in _translate_events(stream):
                    yield chunk

        return _gen()


__all__ = [
    "AnthropicBackend",
]
