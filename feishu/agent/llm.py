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

from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator, Literal, Protocol, Sequence, Union, runtime_checkable

Role = Literal["user", "assistant", "tool"]


@dataclass(slots=True)
class TextPart:
    r"""
    消息中的一段纯文本内容。

    Examples:
        >>> TextPart(text="你好").text
        '你好'
    """

    text: str


@dataclass(slots=True)
class ToolUsePart:
    r"""
    模型发起的一次工具调用，作为助手消息的一个内容块。

    `arguments` 为已解析的参数字典；与 [feishu.agent.llm.ToolCall][] 不同，此处的参数已经是 `dict`，
    而非待解析的 JSON 字符串。

    Examples:
        >>> part = ToolUsePart(id="call_1", name="weather", arguments={"city": "上海"})
        >>> part.name
        'weather'
        >>> part.arguments
        {'city': '上海'}
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class ToolResultPart:
    r"""
    工具执行结果，作为工具消息（`role="tool"`）的内容块回传给模型。

    `tool_call_id` 须与触发执行的 [feishu.agent.llm.ToolUsePart][] 的 `id` 对应。当工具执行失败时，
    将 `is_error` 置为 `True`，模型即可据此调整后续行为。

    Examples:
        >>> ok = ToolResultPart(tool_call_id="call_1", content="晴")
        >>> ok.is_error
        False
        >>> err = ToolResultPart(tool_call_id="call_1", content="超时", is_error=True)
        >>> err.is_error
        True
    """

    tool_call_id: str
    content: str
    is_error: bool = False


ContentPart = Union[TextPart, ToolUsePart, ToolResultPart]


@dataclass(slots=True)
class Message:
    r"""
    一条对话消息，由角色与若干内容块组成。

    `role` 取 `user`、`assistant` 或 `tool` 之一；`content` 是 [feishu.agent.llm.ContentPart][] 列表，
    适配器会将其翻译为各家大模型 API 的消息格式。

    Examples:
        >>> msg = Message(role="user", content=[TextPart(text="你好")])
        >>> msg.role
        'user'
        >>> msg.content[0].text
        '你好'
    """

    role: Role
    content: list[ContentPart]


@dataclass(slots=True)
class ToolSpec:
    r"""
    工具的与厂商无关的声明，供模型据此决定是否调用。

    `input_schema` 为描述参数的 JSON Schema。适配器会将其翻译为各家大模型所需的工具格式
    （Anthropic 的 `input_schema`、OpenAI 的 `function.parameters`）。

    Examples:
        >>> spec = ToolSpec(
        ...     name="weather",
        ...     description="查询天气",
        ...     input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
        ... )
        >>> spec.name
        'weather'
    """

    name: str
    description: str
    input_schema: dict[str, Any]


class StopReason(str, Enum):
    r"""
    归一化后的模型停止原因。

    各适配器会将厂商返回的原始停止原因映射到这些枚举值。由于继承自 `str`，枚举成员可直接与对应的
    字符串字面量比较，便于序列化与传输。

    Examples:
        >>> StopReason.TOOL_USE == "tool_use"
        True
        >>> StopReason("end_turn") is StopReason.END_TURN
        True
    """

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    OTHER = "other"


@dataclass(slots=True)
class TextDelta:
    r"""
    流式响应中的一个文本增量片段。

    Examples:
        >>> TextDelta(text="你").text
        '你'
    """

    text: str


@dataclass(slots=True)
class ToolCallDelta:
    r"""
    流式响应中的一个工具调用增量片段。

    同一次工具调用的多个片段共享相同的 `index`；`id` 与 `name` 通常仅在首个片段出现，而 `arguments`
    会逐段累积成完整的参数 JSON 字符串。[feishu.agent.loop.accumulate_stream][] 负责按 `index` 归并这些片段。

    Examples:
        >>> head = ToolCallDelta(index=0, id="c1", name="weather", arguments='{"ci')
        >>> tail = ToolCallDelta(index=0, arguments='ty":"上海"}')
        >>> head.id, tail.arguments
        ('c1', 'ty":"上海"}')
    """

    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""


@dataclass(slots=True)
class MessageStop:
    r"""
    流式响应的终止信号，携带停止原因与可选的用量统计。

    Examples:
        >>> stop = MessageStop(stop_reason=StopReason.END_TURN, usage={"output_tokens": 12})
        >>> stop.stop_reason
        <StopReason.END_TURN: 'end_turn'>
        >>> stop.usage
        {'output_tokens': 12}
    """

    stop_reason: StopReason
    usage: dict[str, int] | None = None


StreamChunk = Union[TextDelta, ToolCallDelta, MessageStop]


@dataclass(slots=True)
class ToolCall:
    r"""
    由流式片段归并而成的完整工具调用。

    与 [feishu.agent.llm.ToolUsePart][] 不同，此处的 `arguments` 为完整的 JSON 字符串，由
    [feishu.agent.loop.Agent][] 在分发前 `json.loads()` 解析。

    Examples:
        >>> call = ToolCall(id="c1", name="weather", arguments='{"city":"上海"}')
        >>> call.arguments
        '{"city":"上海"}'
    """

    id: str
    name: str
    arguments: str  # complete JSON string; the loop json.loads() it


@runtime_checkable
class LlmBackend(Protocol):
    r"""
    大模型后端协议，是自定义模型后端的扩展契约。

    实现该协议即可接入 [feishu.agent.loop.Agent][]；内置实现见 [feishu.agent.adapters.anthropic.AnthropicBackend][]
    与 [feishu.agent.adapters.openai.OpenAIBackend][]。`stream` 须返回逐个产出 [feishu.agent.llm.StreamChunk][]
    的异步迭代器。该协议标注了 `runtime_checkable`，可用 `isinstance` 校验实现是否符合契约。

    Examples:
        >>> class EchoBackend:
        ...     def stream(self, *, messages, tools=(), system=None, **kwargs):
        ...         async def gen():
        ...             yield TextDelta(text="hi")
        ...         return gen()
        >>> isinstance(EchoBackend(), LlmBackend)
        True
    """

    def stream(
        self,
        *,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        r"""
        以流式方式生成一轮模型响应。

        Args:
            messages: 截至当前轮次的对话历史。
            tools: 本轮可供模型调用的工具声明。
            system: 系统提示词。
            **kwargs: 透传给底层大模型 API 的额外参数。

        Returns:
            逐个产出 [feishu.agent.llm.StreamChunk][] 的异步迭代器。
        """
        ...
