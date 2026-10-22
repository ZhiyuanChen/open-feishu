from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from feishu.agent.adapters.openai import OpenAIBackend
from feishu.agent.llm import MessageStop, TextDelta


class _Source:
    """A provider stream that owns a real HTTP response until explicitly closed."""

    def __init__(self, *, error: Exception | None = None, blocked: bool = False, close_error: bool = False):
        self.response = httpx.Response(200, stream=httpx.ByteStream(b""))
        self.error = error
        self.blocked = blocked
        self.close_error = close_error
        self.waiting = asyncio.Event()
        self.first = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.first:
            self.first = False
            return SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="hello"), finish_reason=None)], usage=None
            )
        if self.blocked:
            self.waiting.set()
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error
        raise StopAsyncIteration

    async def _release(self):
        await self.response.aclose()
        if self.close_error:
            raise RuntimeError("response cleanup failed")


class _SdkStream(_Source):
    async def close(self):
        await self._release()


class _HttpStream(_Source):
    async def aclose(self):
        await self._release()


def _backend(source):
    async def create(**_params):
        return source

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return OpenAIBackend(client=client, model="test-model")


@pytest.mark.parametrize("source_type", [_SdkStream, _HttpStream])
@pytest.mark.parametrize("close_error", [False, True])
async def test_consumer_closing_early_releases_provider_response(source_type, close_error):
    source = source_type(close_error=close_error)
    stream = _backend(source).stream(messages=[])

    assert await anext(stream) == TextDelta(text="hello")
    await stream.aclose()

    assert source.response.is_closed


@pytest.mark.parametrize("source_type", [_SdkStream, _HttpStream])
async def test_complete_stream_releases_provider_response(source_type):
    source = source_type()

    chunks = [chunk async for chunk in _backend(source).stream(messages=[])]

    assert chunks[0] == TextDelta(text="hello")
    assert isinstance(chunks[-1], MessageStop)
    assert source.response.is_closed


@pytest.mark.parametrize("source_type", [_SdkStream, _HttpStream])
async def test_cleanup_error_on_normal_completion_remains_visible(source_type):
    source = source_type(close_error=True)

    with pytest.raises(RuntimeError, match="response cleanup failed"):
        _ = [chunk async for chunk in _backend(source).stream(messages=[])]

    assert source.response.is_closed


@pytest.mark.parametrize("source_type", [_SdkStream, _HttpStream])
@pytest.mark.parametrize("close_error", [False, True])
async def test_source_error_releases_response_and_preserves_original_error(source_type, close_error):
    error = ValueError("provider stream disconnected")
    source = source_type(error=error, close_error=close_error)

    with pytest.raises(ValueError) as raised:
        _ = [chunk async for chunk in _backend(source).stream(messages=[])]

    assert raised.value is error
    assert source.response.is_closed


@pytest.mark.parametrize("source_type", [_SdkStream, _HttpStream])
@pytest.mark.parametrize("close_error", [False, True])
async def test_cancelled_consumption_releases_response_and_preserves_cancellation(source_type, close_error):
    source = source_type(blocked=True, close_error=close_error)

    async def consume():
        return [chunk async for chunk in _backend(source).stream(messages=[])]

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(source.waiting.wait(), timeout=1)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        assert source.response.is_closed
    finally:
        if not consumer.done():
            consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


async def test_iterable_without_close_remains_supported():
    class Source:
        def __aiter__(self):
            async def chunks():
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="hello"), finish_reason="stop")], usage=None
                )

            return chunks()

    chunks = [chunk async for chunk in _backend(Source()).stream(messages=[])]

    assert chunks[0] == TextDelta(text="hello")
    assert isinstance(chunks[-1], MessageStop)
