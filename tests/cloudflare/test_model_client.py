from __future__ import annotations

import asyncio
import json
from importlib import import_module

import httpx
import pytest

from feishu.agent.adapters.openai import OpenAIBackend
from feishu.agent.llm import Message, MessageStop, StopReason, TextDelta, TextPart, ToolCallDelta


def _client(handler, *, timeout=12.0):
    cls = import_module("examples.cloudflare.src.model_client").ChatCompletionsClient
    return cls(
        api_key="test-key",
        base_url="https://models.example/v1/",
        timeout=timeout,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


class _Body(httpx.AsyncByteStream):
    def __init__(self, *parts: bytes) -> None:
        self.parts = parts
        self.closed = False

    async def __aiter__(self):
        for part in self.parts:
            yield part

    async def aclose(self) -> None:
        self.closed = True


async def test_completion_posts_provider_options_and_returns_attribute_views() -> None:
    received = []

    def handler(request):
        received.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "answer"}}], "usage": {"total_tokens": 7}},
        )

    client = _client(handler)
    try:
        response = await client.chat.completions.create(
            model="m",
            messages=[{"role": "user", "content": "hello"}],
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
        assert response.choices[0].message.content == "answer"
        assert response.usage.total_tokens == 7
        assert str(received[0].url) == "https://models.example/v1/chat/completions"
        assert received[0].headers["authorization"] == "Bearer test-key"
        assert received[0].extensions["timeout"]["read"] == 12.0
        assert json.loads(received[0].content) == {
            "model": "m",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        }
    finally:
        await client.aclose()


async def test_stream_parses_split_lines_multiline_data_and_done_before_extra_bytes() -> None:
    body = _Body(
        b': keepalive\r\ndata: {"cho',
        b'ices": [\r\ndata: {"delta": {"content": "hello"}, "finish_reason": null}]}\r\n\r',
        b'\ndata: {"choices": [], "usage": {"total_tokens": 3}}\n\ndata: [DONE]\n\n',
        b"data: invalid after terminal marker\n\n",
    )
    client = _client(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body))
    try:
        stream = await client.chat.completions.create(model="m", stream=True)
        chunks = [chunk async for chunk in stream]
        assert len(chunks) == 2
        assert chunks[0].choices[0].delta.content == "hello"
        assert chunks[1].usage.total_tokens == 3
        assert body.closed
    finally:
        await client.aclose()


async def test_http_stream_reuses_existing_backend_tool_and_usage_translation() -> None:
    body = _Body(
        b'data: {"choices":[{"delta":{"content":"hi","tool_calls":[{"index":0,"id":"call",'
        b'"function":{"name":"lookup","arguments":"{}"}}]},"finish_reason":null}]}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n',
        b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n',
        b"data: [DONE]\n\n",
    )
    client = _client(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body))
    try:
        backend = OpenAIBackend(client=client, model="m")
        chunks = [chunk async for chunk in backend.stream(messages=[Message(role="user", content=[TextPart("q")])])]
        assert chunks == [
            TextDelta(text="hi"),
            ToolCallDelta(index=0, id="call", name="lookup", arguments="{}"),
            MessageStop(
                stop_reason=StopReason.TOOL_USE,
                usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            ),
        ]
        assert body.closed
    finally:
        await client.aclose()


@pytest.mark.parametrize("streaming", [False, True])
async def test_http_error_closes_response_without_retry(streaming) -> None:
    body = _Body(b'{"error":{"message":"unavailable"}}')
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, stream=body)

    client = _client(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.chat.completions.create(model="m", stream=streaming)
        assert len(calls) == 1
        assert body.closed
    finally:
        await client.aclose()


@pytest.mark.parametrize("payload", [b"not json", b"[]", b'{"error":{"message":"failed"}}'])
async def test_invalid_or_error_completion_json_is_rejected(payload) -> None:
    body = _Body(payload)
    client = _client(lambda request: httpx.Response(200, stream=body))
    try:
        with pytest.raises(ValueError):
            await client.chat.completions.create(model="m")
        assert body.closed
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "payload",
    [
        b"data: invalid json\n\n",
        b"data: []\n\n",
        b'data: {"error":{"message":"failed"}}\n\n',
        b'event: error\ndata: {"message":"failed"}\n\n',
        b'data: {"choices":[]}\n\n',
    ],
)
async def test_invalid_error_or_truncated_sse_is_rejected_and_closed(payload) -> None:
    body = _Body(payload)
    client = _client(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body))
    try:
        stream = await client.chat.completions.create(model="m", stream=True)
        with pytest.raises(ValueError):
            [chunk async for chunk in stream]
        assert body.closed
    finally:
        await client.aclose()


async def test_stream_rejects_non_sse_response_and_closes_it() -> None:
    body = _Body(b'{"choices":[]}')
    client = _client(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, stream=body))
    try:
        with pytest.raises(ValueError):
            await client.chat.completions.create(model="m", stream=True)
        assert body.closed
    finally:
        await client.aclose()


@pytest.mark.parametrize("consume_first", [False, True])
async def test_explicit_stream_close_releases_unconsumed_response(consume_first) -> None:
    body = _Body(b'data: {"choices":[]}\n\n', b"data: [DONE]\n\n")
    client = _client(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body))
    try:
        stream = await client.chat.completions.create(model="m", stream=True)
        if consume_first:
            await anext(stream)
        await stream.aclose()
        assert body.closed
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    finally:
        await client.aclose()


async def test_cancellation_while_waiting_for_sse_closes_response() -> None:
    entered = asyncio.Event()

    class WaitingBody(_Body):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b""

    body = WaitingBody()
    client = _client(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body))
    try:
        stream = await client.chat.completions.create(model="m", stream=True)
        task = asyncio.create_task(anext(stream))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert body.closed
    finally:
        await client.aclose()


async def test_client_close_releases_outstanding_stream_and_http_client() -> None:
    body = _Body(b'data: {"choices":[]}\n\n', b"data: [DONE]\n\n")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
    )
    http_client = httpx.AsyncClient(transport=transport)
    cls = import_module("examples.cloudflare.src.model_client").ChatCompletionsClient
    client = cls(api_key="test-key", base_url="https://models.example/v1", http_client=http_client)
    stream = await client.chat.completions.create(model="m", stream=True)
    await client.aclose()
    assert body.closed
    assert http_client.is_closed
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    with pytest.raises(RuntimeError):
        await client.chat.completions.create(model="m")


class _FailingCloseBody(_Body):
    async def aclose(self) -> None:
        self.closed = True
        raise RuntimeError("stream close failed")


@pytest.mark.parametrize("streaming", [False, True])
async def test_http_error_survives_response_close_error(streaming) -> None:
    body = _FailingCloseBody(b'{"error":{"message":"unavailable"}}')
    client = _client(lambda request: httpx.Response(503, stream=body))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.chat.completions.create(model="m", stream=streaming)
        assert body.closed
    finally:
        await client.aclose()


async def test_sse_json_error_survives_response_close_error() -> None:
    body = _FailingCloseBody(b"data: invalid json\n\n")
    client = _client(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body))
    try:
        stream = await client.chat.completions.create(model="m", stream=True)
        with pytest.raises(json.JSONDecodeError):
            await anext(stream)
        assert body.closed
    finally:
        await client.aclose()


async def test_nonstream_read_error_survives_response_close_error() -> None:
    class FailingReadBody(_FailingCloseBody):
        async def __aiter__(self):
            yield b'{"choices":'
            raise httpx.ReadError("model read failed")

    body = FailingReadBody()
    client = _client(lambda request: httpx.Response(200, stream=body))
    try:
        with pytest.raises(httpx.ReadError, match="model read failed"):
            await client.chat.completions.create(model="m")
        assert body.closed
    finally:
        await client.aclose()


async def test_cancellation_survives_response_close_error() -> None:
    entered = asyncio.Event()

    class WaitingBody(_FailingCloseBody):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b""

    body = WaitingBody()
    client = _client(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body))
    try:
        stream = await client.chat.completions.create(model="m", stream=True)
        task = asyncio.create_task(anext(stream))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert body.closed
    finally:
        await client.aclose()


async def test_normal_stream_completion_propagates_close_error() -> None:
    body = _FailingCloseBody(b"data: [DONE]\n\n")
    client = _client(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body))
    try:
        stream = await client.chat.completions.create(model="m", stream=True)
        with pytest.raises(RuntimeError, match="stream close failed"):
            await anext(stream)
        assert body.closed
    finally:
        await client.aclose()


async def test_client_closes_every_stream_and_http_client_despite_multiple_close_errors() -> None:
    bodies = [_FailingCloseBody(), _FailingCloseBody()]
    pending = iter(bodies)

    class FailingCloseTransport(httpx.MockTransport):
        closed = False

        async def aclose(self):
            self.closed = True
            raise LookupError("HTTP client close failed")

    transport = FailingCloseTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=next(pending))
    )
    http_client = httpx.AsyncClient(transport=transport)
    cls = import_module("examples.cloudflare.src.model_client").ChatCompletionsClient
    client = cls(api_key="test-key", base_url="https://models.example/v1", http_client=http_client)
    streams = [await client.chat.completions.create(model="m", stream=True) for _ in bodies]
    with pytest.raises(RuntimeError, match="stream close failed"):
        await client.aclose()
    assert all(body.closed for body in bodies)
    assert http_client.is_closed
    assert transport.closed
    for stream in streams:
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
