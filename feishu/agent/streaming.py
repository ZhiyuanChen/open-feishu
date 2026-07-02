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

"""将 LLM 流式响应转换为文本增量的通用适配器。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Iterator

from .adapters.anthropic import _translate_events as _anthropic_translate
from .adapters.openai import _translate_chunks as _openai_translate
from .llm import ReasoningDelta, TextDelta

__all__ = ["stream_text"]

_SENTINEL = object()


def _looks_like_openai_chunk(item: Any) -> bool:
    """判断单个 chunk 是否是 OpenAI ChatCompletionChunk（有 choices 列表）。"""
    return hasattr(item, "choices") and isinstance(getattr(item, "choices", None), list)


def _looks_like_openai_stream_by_module(obj: Any) -> bool:
    """通过模块名启发式判断对象是否来自 OpenAI SDK。"""
    module = getattr(type(obj), "__module__", "") or ""
    return "openai" in module.lower()


def _next_or_sentinel(it: Any) -> Any:
    """调用 next(it)，耗尽时返回 _SENTINEL 而非抛出 StopIteration。

    在 Python 3.14+ 中，StopIteration 无法通过 Future 传播；此函数用于在
    run_in_executor 中安全地步进同步迭代器。
    """
    try:
        return next(it)
    except StopIteration:
        return _SENTINEL


async def _sync_to_async(iterable: Any) -> AsyncIterator[Any]:
    """将同步可迭代对象包装为异步迭代器，通过线程池避免阻塞事件循环。"""
    loop = asyncio.get_event_loop()
    it = iter(iterable)
    while True:
        item = await loop.run_in_executor(None, _next_or_sentinel, it)
        if item is _SENTINEL:
            return
        yield item


async def _yield_text_from_openai(async_iter: AsyncIterator[Any]) -> AsyncIterator[str]:
    """经 OpenAI 适配器归一化后，仅产出文本增量。"""
    async for chunk in _openai_translate(async_iter):
        if isinstance(chunk, TextDelta):
            yield chunk.text


async def _yield_text_from_anthropic(async_iter: AsyncIterator[Any]) -> AsyncIterator[str]:
    """经 Anthropic 适配器归一化后，仅产出文本增量。"""
    async for chunk in _anthropic_translate(async_iter):
        if isinstance(chunk, TextDelta):
            yield chunk.text


async def stream_text(provider_stream: Any) -> AsyncIterator[str]:
    r"""
    将任意 LLM 提供商的流式响应转换为纯文本增量的异步迭代器。

    本函数是 [feishu.client.FeishuClient.stream_card][] 的配套适配器：将大模型流式
    响应（同步或异步）归一化为逐个字符串 token，可直接传入 ``stream_card`` 的
    ``tokens`` 参数，从而把 LLM 输出实时推送至飞书消息卡片。

    适配策略：

    - **Anthropic 上下文管理器**（``AsyncMessageStreamManager``，带 ``__aenter__``
      但无 ``__aiter__``）：进入上下文后迭代原始 SSE 事件，经
      `feishu.agent.adapters.anthropic._translate_events` 归一化。

    - **OpenAI 异步流**（``AsyncStream[ChatCompletionChunk]`` 或 chunk 带
      ``choices`` 属性）：直接异步迭代，经
      `feishu.agent.adapters.openai._translate_chunks` 归一化。

    - **OpenAI 同步流**（``Stream[ChatCompletionChunk]``，同步可迭代，首个元素有
      ``choices`` 属性）：通过 ``loop.run_in_executor`` 包装后同上处理。

    - **归一化 StreamChunk 序列**（已经 [feishu.agent.llm.LlmBackend][] 适配器处理
      的异步迭代器，首个元素为 [feishu.agent.llm.TextDelta][]）：直接消费，仅产出
      ``TextDelta.text``，跳过工具调用增量与停止信号。

    - **Anthropic 原始事件异步/同步流**（默认 fallback）：经 Anthropic 适配器归一化。

    同步/异步均兼容。工具调用增量（[feishu.agent.llm.ToolCallDelta][]）与停止信号
    （[feishu.agent.llm.MessageStop][]）均被静默跳过，只有文本内容被产出。

    Args:
        provider_stream: LLM 提供商返回的流式对象，支持以下类型：

            - ``openai.AsyncStream[ChatCompletionChunk]``
            - ``openai.Stream[ChatCompletionChunk]``（同步）
            - ``anthropic.AsyncMessageStreamManager``（``async with`` 风格）
            - 产出原始 Anthropic SSE 事件的异步/同步可迭代对象
            - 产出 [feishu.agent.llm.StreamChunk][] 的异步迭代器
              （即 [feishu.agent.llm.LlmBackend.stream][] 的返回值）

    Returns:
        逐个产出文本增量字符串（``str``）的异步迭代器，不含工具调用增量或停止信号。

    Raises:
        TypeError: 当 ``provider_stream`` 既非异步可迭代也非同步可迭代时抛出。

    飞书文档:
        [CardKit 流式更新](https://open.feishu.cn/document/cardkit-v1/streaming-updates-openapi-overview)

    Examples:
        >>> import asyncio
        >>> from feishu.agent.llm import TextDelta, MessageStop, StopReason
        >>> async def _fake_normalized():
        ...     yield TextDelta(text="Hello")
        ...     yield TextDelta(text=" world")
        ...     yield MessageStop(stop_reason=StopReason.END_TURN)
        >>> async def _run():
        ...     return [t async for t in stream_text(_fake_normalized())]
        >>> asyncio.run(_run())
        ['Hello', ' world']
    """
    # ------------------------------------------------------------------
    # Case 1: Anthropic async-context-manager (e.g. AsyncMessageStreamManager).
    # Has __aenter__ but is NOT directly async-iterable.
    # ------------------------------------------------------------------
    is_async_iterable = hasattr(provider_stream, "__aiter__")
    is_context_manager = hasattr(provider_stream, "__aenter__")

    if is_context_manager and not is_async_iterable:
        async with provider_stream as raw_stream:
            async for text in _yield_text_from_anthropic(raw_stream):
                yield text
        return

    # ------------------------------------------------------------------
    # Case 2: Async iterable — detect provider by peeking at first chunk.
    # ------------------------------------------------------------------
    if is_async_iterable:
        async for text in _stream_text_async(provider_stream):
            yield text
        return

    # ------------------------------------------------------------------
    # Case 3: Synchronous iterable — wrap with run_in_executor, then detect.
    # ------------------------------------------------------------------
    if hasattr(provider_stream, "__iter__"):
        async for text in _stream_text_sync(provider_stream):
            yield text
        return

    raise TypeError(
        f"stream_text: unsupported provider_stream type {type(provider_stream)!r}; "
        "expected an async/sync iterable of LLM stream chunks."
    )


async def _stream_text_async(async_iter: AsyncIterator[Any]) -> AsyncIterator[str]:
    """异步流的自动分派：OpenAI chunks、Anthropic 原始事件、归一化 StreamChunk。

    缓冲首个元素以判断流类型，然后重放完整序列。
    """
    first: list[Any] = []

    async for item in async_iter:
        first.append(item)
        break

    if not first:
        return  # empty stream

    head = first[0]

    async def _replay() -> AsyncIterator[Any]:
        # Re-emit the peeked head, then stream the remainder without buffering it.
        yield head
        async for item in async_iter:
            yield item

    if _looks_like_openai_chunk(head) or _looks_like_openai_stream_by_module(async_iter):
        # OpenAI ChatCompletionChunk: has a .choices list
        async for text in _yield_text_from_openai(_replay()):
            yield text
    elif isinstance(head, (TextDelta, ReasoningDelta)):
        # Already-normalized StreamChunk iterator from LlmBackend.stream()
        async for item in _replay():
            if isinstance(item, TextDelta):
                yield item.text
    else:
        # Default: raw Anthropic SSE events
        async for text in _yield_text_from_anthropic(_replay()):
            yield text


async def _stream_text_sync(sync_iter: Any) -> AsyncIterator[str]:
    """同步流的自动分派：从首个元素判断 OpenAI 还是 Anthropic。"""
    it = iter(sync_iter)
    try:
        head = next(it)
    except StopIteration:
        return  # empty

    # Rebuild a sync iterator that replays head then the rest
    def _prepend() -> Iterator[Any]:
        yield head
        yield from it

    async_wrapped = _sync_to_async(_prepend())

    if _looks_like_openai_chunk(head) or _looks_like_openai_stream_by_module(sync_iter):
        async for text in _yield_text_from_openai(async_wrapped):
            yield text
    else:
        async for text in _yield_text_from_anthropic(async_wrapped):
            yield text
