"""The Chat Completions HTTP subset used by OpenFeishu, without importing the model SDK."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import suppress
from types import SimpleNamespace
from typing import Any

import httpx


def _attribute_view(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _attribute_view(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_attribute_view(item) for item in value]
    return value


def _completion_object(data: str | bytes) -> Any:
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ValueError("model response must be a JSON object")
    if "error" in payload:
        raise ValueError("model API returned an error")
    return _attribute_view(payload)


class _CompletionStream:
    """Own the response independently of iteration, including a stream closed before its first item."""

    def __init__(self, response: httpx.Response, on_close: Callable[[_CompletionStream], None]) -> None:
        self._response = response
        self._on_close = on_close
        self._lines = response.aiter_lines()
        self._data: list[str] = []
        self._event = ""
        self._closed = False

    def __aiter__(self) -> _CompletionStream:
        return self

    async def __anext__(self) -> Any:
        if self._closed:
            raise StopAsyncIteration
        try:
            while not self._closed:
                try:
                    line = await anext(self._lines)
                except StopAsyncIteration as exc:
                    raise ValueError("model event stream ended before [DONE]") from exc
                if line:
                    field, _, value = line.partition(":")
                    if value.startswith(" "):
                        value = value[1:]
                    if field == "data":
                        self._data.append(value)
                    elif field == "event":
                        self._event = value
                    continue
                data, event = self._data, self._event
                self._data, self._event = [], ""
                if event == "error":
                    raise ValueError("model event stream returned an error")
                if not data:
                    continue
                payload = "\n".join(data)
                if payload.strip() == "[DONE]":
                    await self.aclose()
                    raise StopAsyncIteration
                return _completion_object(payload)
        except BaseException:
            # Cleanup must not replace a model/protocol failure or caller cancellation.
            with suppress(BaseException):
                await self.aclose()
            raise
        raise StopAsyncIteration

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._response.aclose()
        finally:
            self._on_close(self)


class ChatCompletionsClient:
    """Provide only ``chat.completions.create`` and close, using an owned HTTP client.

    Model and tool translation remains in OpenFeishu's ``OpenAIBackend``. An injected
    ``http_client`` is also closed by this client. Requests are never automatically retried.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._timeout = timeout
        self._http_client = http_client if http_client is not None else httpx.AsyncClient()
        self._streams: set[_CompletionStream] = set()
        self._closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **params: Any) -> Any:
        if self._closed:
            raise RuntimeError("model client is closed")
        extra_body = params.pop("extra_body", None)
        if extra_body is not None:
            if not isinstance(extra_body, Mapping):
                raise TypeError("extra_body must be a mapping")
            params.update(extra_body)
        streaming = bool(params.get("stream", False))
        request = self._http_client.build_request(
            "POST",
            self._url,
            json=params,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "text/event-stream" if streaming else "application/json",
            },
            timeout=self._timeout,
        )
        response = await self._http_client.send(request, stream=True)
        try:
            response.raise_for_status()
            if streaming:
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type != "text/event-stream":
                    raise ValueError("model API did not return a server-sent event stream")
                stream = _CompletionStream(response, self._streams.discard)
                self._streams.add(stream)
                return stream
            completion = _completion_object(await response.aread())
        except BaseException:
            with suppress(BaseException):
                await response.aclose()
            raise
        await response.aclose()
        return completion

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: BaseException | None = None
        closers = [stream.aclose for stream in tuple(self._streams)]
        closers.append(self._http_client.aclose)
        for close in closers:
            try:
                await close()
            except BaseException as exc:  # noqa: B036 - re-raise after every remaining resource was attempted
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
