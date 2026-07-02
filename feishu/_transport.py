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
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from chanfig import NestedDict

from .consts import API_PREFIX, DEFAULT_TIMEOUT
from .errors import (
    FeishuRateLimitError,
    FeishuServerError,
    FeishuTransportError,
    error_from_envelope,
)

# Idempotent HTTP methods whose network errors and 5xx responses can be safely retried. Repeating these
# requests should not create extra side effects; non-idempotent POST/PATCH-style methods are excluded because
# resending them may commit duplicate writes, such as creating the same resource twice.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE"})


@dataclass
class RetryPolicy:
    r"""
    传输层重试策略。

    定义对可重试错误（HTTP 5xx 与 429 限流、网络错误）的最大重试次数与退避时间。
    退避采用指数增长并以 `max_delay` 封顶；启用 `jitter` 时在区间内随机抖动以避免
    雷鸣效应。若服务器给出了限流重置提示，则优先使用该提示值（同样以 `max_delay` 封顶）
    作为等待时间。`max_elapsed` 为整个重试过程的总耗时预算上限，一旦累计退避耗时超过该值
    便不再继续重试，避免在持续故障时无限拖延。

    Args:
        max_attempts: 最大尝试次数（含首次请求），默认 3。
        base_delay: 退避基准时间（秒），默认 0.5。
        max_delay: 单次退避时间上限（秒），默认 30.0。
        jitter: 是否对退避时间施加随机抖动，默认 `True`。
        max_elapsed: 整个重试过程的总退避耗时预算上限（秒）；为 `None` 时取
            `max_delay * max_attempts`。超过该预算后停止重试。

    Examples:
        >>> policy = RetryPolicy(base_delay=0.5, jitter=False)
        >>> policy.delay(1, None)
        0.5
        >>> policy.delay(3, None)
        2.0
        >>> policy.delay(1, reset_after=7.0)
        7.0
        >>> RetryPolicy(max_delay=5.0, jitter=False).delay(1, reset_after=7.0)
        5.0
    """

    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    jitter: bool = True
    max_elapsed: float | None = None

    @classmethod
    def default(cls) -> RetryPolicy:
        r"""
        返回使用全部默认参数的重试策略。

        Returns:
            默认配置的 [feishu.RetryPolicy][] 实例。

        Examples:
            >>> RetryPolicy.default().max_attempts
            3
        """
        return cls()

    @property
    def elapsed_budget(self) -> float:
        r"""
        整个重试过程的总退避耗时预算（秒）。

        若显式设置了 `max_elapsed` 则采用之，否则回退到 `max_delay * max_attempts`。

        Returns:
            总退避耗时预算（秒）。

        Examples:
            >>> RetryPolicy(max_delay=30.0, max_attempts=3).elapsed_budget
            90.0
            >>> RetryPolicy(max_elapsed=10.0).elapsed_budget
            10.0
        """
        if self.max_elapsed is not None:
            return self.max_elapsed
        return self.max_delay * self.max_attempts

    def delay(self, attempt: int, reset_after: float | None) -> float:
        r"""
        计算第 `attempt` 次重试前应等待的秒数。

        若提供了 `reset_after`（服务器限流重置提示），则采用该值并以 `max_delay` 封顶，
        防止异常大的服务端提示导致过长等待；否则按 `base_delay * 2 ** (attempt - 1)`
        指数退避并以 `max_delay` 封顶，启用 `jitter` 时再在 `[0, delay]` 区间内随机取值。

        Args:
            attempt: 当前重试序号，从 1 开始。
            reset_after: 服务器给出的限流重置等待秒数；为 `None` 时按指数退避计算。

        Returns:
            建议等待的秒数。

        Examples:
            >>> RetryPolicy(base_delay=0.5, jitter=False).delay(2, None)
            1.0
            >>> RetryPolicy(max_delay=5.0).delay(1, reset_after=7.0)
            5.0
        """
        if reset_after is not None:
            return min(self.max_delay, reset_after)
        delay = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
        if self.jitter:
            delay = random.uniform(0, delay) if delay else 0.0
        return delay


class Transport:
    r"""
    基于 httpx 的飞书 HTTP 传输层。

    负责拼接 Open API 完整 URL、注入 Bearer 令牌、解析响应信封，并依据
    [feishu.RetryPolicy][] 退避重试：429 / 限流码可重试所有方法，网络错误与 5xx
    仅重试幂等方法。若 `client`
    由调用方传入，则其生命周期由调用方管理；否则由本传输层创建并在
    [feishu._transport.Transport.aclose][] 时关闭。

    Args:
        base_url: API 基础地址（不含 Open API 路径前缀）。
        timeout: 请求超时时间（秒）。
        retry: 重试策略，缺省使用 [feishu.RetryPolicy.default][]。
        client: 自定义 `httpx.AsyncClient`；提供后其生命周期不归本传输层管理。
        logger: 自定义日志器，缺省使用名为 `feishu` 的日志器。
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retry: RetryPolicy | None = None,
        client: httpx.AsyncClient | None = None,
        logger: logging.Logger | None = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retry = retry or RetryPolicy.default()
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self.logger = logger or logging.getLogger("feishu")
        self._sleep = sleep or asyncio.sleep

    async def aclose(self) -> None:
        r"""
        关闭底层 HTTP 客户端。

        仅当 `httpx.AsyncClient` 由本传输层创建时才会关闭；调用方传入的客户端不会被关闭。
        """
        if self._owns_client:
            await self._client.aclose()

    def _url(self, path: str) -> str:
        # Absolute URLs pass through unchanged: a few Feishu endpoints (e.g. approval file upload) live on a
        # different host outside the /open-apis prefix. Relative paths get the standard Open API prefix.
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.base_url}{API_PREFIX}/{path.lstrip('/')}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        token: str | None = None,
        headers: Mapping[str, str] | None = None,
        expect_envelope: bool = True,
    ) -> NestedDict:
        r"""
        发送一次 HTTP 请求并返回解析后的响应体，按需自动重试。

        将 `path` 拼接到 Open API 前缀下组成完整 URL；若提供 `token` 则注入
        `Authorization: Bearer` 请求头；值为 `None` 的查询参数会被剔除。对返回
        429 / 限流码会按重试策略退避后重试；网络错误与 5xx 仅对幂等方法重试。
        重试耗尽则抛出对应异常；业务错误（如鉴权、权限、普通业务码）则立即抛出，不重试。

        Args:
            method: HTTP 方法。
            path: 接口路径（相对于 Open API 前缀）。
            params: URL 查询参数；值为 `None` 的项会被剔除。
            json: 请求体（将以 JSON 编码）。
            token: 访问令牌；非空时注入 `Authorization: Bearer` 请求头。
            headers: 附加请求头。
            expect_envelope: 是否按标准 `{code, msg, data}` 信封判定成功；返回裸
                `{access_token, ...}` 的接口应设为 `False`。

        Returns:
            解析后的响应体，类型为 `chanfig.NestedDict`。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。
            feishu.errors.FeishuTransportError: 网络错误重试耗尽时抛出。
        """
        url = self._url(path)
        hdrs = dict(headers or {})
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        if json is not None:
            hdrs["Content-Type"] = "application/json"
        if params is not None:
            params = {k: v for k, v in params.items() if v is not None}

        def classify(resp: httpx.Response) -> tuple[Any, Exception | None, float | None]:
            log_id = resp.headers.get("x-tt-logid")
            try:
                payload = resp.json()
            except ValueError:
                payload = {"code": -1, "msg": resp.text}
            code = payload.get("code", -1)
            # Success classification differs by mode; the retry path is shared in _send.
            if expect_envelope:
                if code == 0 and resp.status_code < 400:
                    return NestedDict(payload), None, None
            else:
                if resp.status_code < 400 and "access_token" in payload:
                    return NestedDict(payload), None, None
                if resp.status_code < 400 and code == 0 and "error" not in payload:
                    return NestedDict(payload), None, None
            reset_after = _reset_after(resp.headers)
            error_description = payload.get("error_description") if isinstance(payload, dict) else None
            error_exc = error_from_envelope(
                code,
                payload.get("msg", ""),
                status=resp.status_code,
                log_id=log_id,
                raw=payload,
                reset_after=reset_after,
                error_description=error_description,
            )
            return None, error_exc, reset_after

        return await self._send(
            method,
            lambda: self._client.request(method, url, params=params, json=json, headers=hdrs, timeout=self.timeout),
            classify,
        )

    async def download(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        token: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> bytes:
        r"""
        发送 HTTP 请求并返回原始字节（用于资源文件下载）。

        与 request() 共享 URL 拼接、鉴权注入与重试逻辑；成功时直接返回 resp.content
        （不解析 JSON 信封）。若响应码为非 2xx，则尝试按 JSON 错误信封解析后抛出
        对应的 FeishuError；若响应体不是 JSON，则抛出 FeishuTransportError。

        Args:
            method: HTTP 方法。
            path: 接口路径（相对于 Open API 前缀）。
            params: URL 查询参数；值为 None 的项会被剔除。
            token: 访问令牌；非空时注入 Authorization: Bearer 请求头。
            headers: 附加请求头。

        Returns:
            响应体的原始字节。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。
            feishu.errors.FeishuTransportError: 网络错误重试耗尽时抛出。

        Examples:
            >>> import asyncio
            >>> from feishu._transport import Transport
            >>> async def main():
            ...     t = Transport("https://open.feishu.cn")
            ...     return await t.download("GET", "im/v1/messages/om_1/resources/k1", params={"type": "image"})
            >>> asyncio.run(main())  # doctest: +SKIP
            b'...'
        """
        url = self._url(path)
        hdrs = dict(headers or {})
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        if params is not None:
            params = {k: v for k, v in params.items() if v is not None}

        def classify(resp: httpx.Response) -> tuple[Any, Exception | None, float | None]:
            if resp.is_success:
                return resp.content, None, None
            reset_after = _reset_after(resp.headers)
            log_id = resp.headers.get("x-tt-logid")
            try:
                payload = resp.json()
            except ValueError:
                # Non-JSON error body: surface as a transport error via the tuple contract (so the
                # shared retry path applies — a 5xx download on an idempotent GET is retriable).
                return None, FeishuTransportError(f"download failed: {resp.status_code}"), reset_after
            code = payload.get("code", -1) if isinstance(payload, dict) else -1
            msg = payload.get("msg", "") if isinstance(payload, dict) else ""
            error_exc = error_from_envelope(
                code, msg, status=resp.status_code, log_id=log_id, raw=payload, reset_after=reset_after
            )
            return None, error_exc, reset_after

        return await self._send(
            method,
            lambda: self._client.request(method, url, params=params, headers=hdrs, timeout=self.timeout),
            classify,
        )

    async def upload(
        self,
        path: str,
        *,
        data: Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
        token: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> NestedDict:
        r"""
        以 multipart/form-data 方式上传文件并返回解析后的响应体，按需自动重试。

        将 `path` 拼接到 Open API 前缀下组成完整 URL；若提供 `token` 则注入
        `Authorization: Bearer` 请求头。表单字段经 `data` 传入、文件经 `files` 传入，
        由 httpx 负责设置 multipart 边界，调用方不应自行指定 `Content-Type`。响应按
        标准 `{code, msg, data}` 信封判定成功，并复用与 request() 相同的错误分类逻辑：
        对 429 / 限流码退避后重试；网络错误与 5xx 仅对幂等方法重试。重试耗尽则抛出对应异常；
        业务错误立即抛出。

        Args:
            path: 接口路径（相对于 Open API 前缀）。
            data: multipart 表单字段（普通字段）。
            files: multipart 文件字段，形如 `{"file": bytes}` 或 httpx 支持的元组形式。
            token: 访问令牌；非空时注入 `Authorization: Bearer` 请求头。
            headers: 附加请求头；请勿设置 `Content-Type`，由 httpx 自动填充边界。

        Returns:
            解析后的响应体，类型为 `chanfig.NestedDict`。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。
            feishu.errors.FeishuTransportError: 网络错误重试耗尽时抛出。
        """
        url = self._url(path)
        hdrs = dict(headers or {})
        if token:
            hdrs["Authorization"] = f"Bearer {token}"

        def classify(resp: httpx.Response) -> tuple[Any, Exception | None, float | None]:
            log_id = resp.headers.get("x-tt-logid")
            try:
                payload = resp.json()
            except ValueError:
                payload = {"code": -1, "msg": resp.text}
            code = payload.get("code", -1)
            if code == 0 and resp.status_code < 400:
                return NestedDict(payload), None, None
            reset_after = _reset_after(resp.headers)
            error_description = payload.get("error_description") if isinstance(payload, dict) else None
            error_exc = error_from_envelope(
                code,
                payload.get("msg", ""),
                status=resp.status_code,
                log_id=log_id,
                raw=payload,
                reset_after=reset_after,
                error_description=error_description,
            )
            return None, error_exc, reset_after

        # multipart upload is a POST -> non-idempotent: only 429 (request not yet processed) is
        # retried; RequestError / 5xx are not, to avoid duplicating a committed upload.
        return await self._send(
            "POST",
            lambda: self._client.request("POST", url, data=data, files=files, headers=hdrs, timeout=self.timeout),
            classify,
        )

    async def _send(
        self,
        retry_method: str,
        do_request: Callable[[], Awaitable[httpx.Response]],
        classify: Callable[[httpx.Response], tuple[Any, Exception | None, float | None]],
    ) -> Any:
        r"""
        共享的请求-重试驱动：执行 `do_request` 发起一次请求，用 `classify` 将响应判定为成功值
        或错误，并按 [feishu.RetryPolicy][] 对可重试错误退避重试。

        `classify(resp)` 返回三元组 `(success_value, error, reset_after)`：成功时 `error` 为
        `None`、`success_value` 即该次调用的返回值；失败时 `error` 为待抛出/可重试的异常。
        `retry_method` 决定网络错误与 5xx 是否可重试（仅幂等方法）；429 限流对所有方法可重试。
        """
        deadline = self._deadline()
        last_exc: Exception | None = None
        for attempt in range(1, self.retry.max_attempts + 1):
            try:
                resp = await do_request()
            except httpx.RequestError as exc:
                last_exc = FeishuTransportError(f"request failed: {exc}", original=exc)
                planned = self.retry.delay(attempt, None)
                if self._should_retry(retry_method, last_exc, attempt) and not self._past_deadline(deadline, planned):
                    await self._maybe_sleep(planned)
                    continue
                raise last_exc

            value, error_exc, reset_after = classify(resp)
            if error_exc is None:
                return value
            planned = self.retry.delay(attempt, reset_after)
            if self._should_retry(retry_method, error_exc, attempt) and not self._past_deadline(deadline, planned):
                last_exc = error_exc
                await self._maybe_sleep(planned)
                continue
            raise error_exc

        assert last_exc is not None
        raise last_exc

    def _should_retry(self, method: str, exc: Exception, attempt: int) -> bool:
        r"""判定在第 `attempt` 次尝试后是否应对 `exc` 重试 `method` 请求。

        重试规则按幂等性区分：限流（429 / 限流码，请求尚未被处理）对所有方法都可重试；
        网络错误与 5xx 仅对幂等方法（GET/HEAD/PUT/DELETE）重试，因为非幂等方法
        （POST/PATCH）可能已在服务端提交，重试会造成重复写入。仅当仍有剩余尝试次数时才返回真。
        """
        if attempt >= self.retry.max_attempts:
            return False
        if isinstance(exc, FeishuRateLimitError):
            return True
        if isinstance(exc, (FeishuServerError, FeishuTransportError)):
            return method.upper() in IDEMPOTENT_METHODS
        return False

    def _deadline(self) -> float:
        r"""返回本次调用的重试截止时刻（单调时钟秒数）。"""
        return time.monotonic() + self.retry.elapsed_budget

    def _past_deadline(self, deadline: float, planned_delay: float) -> bool:
        r"""判断在退避 `planned_delay` 秒后是否会越过重试时间预算 `deadline`。

        在真正休眠前做预判：若已越过截止时刻，或再休眠 `planned_delay` 会越过截止时刻，
        则不再重试。基于计划退避时长投影预算，使预算判定不依赖墙钟休眠的真实推进，便于测试。
        """
        now = time.monotonic()
        return now >= deadline or now + planned_delay > deadline

    async def _maybe_sleep(self, delay: float) -> None:
        r"""休眠 `delay` 秒（`delay <= 0` 时不休眠）。

        休眠的正是 `_send` 用于截止时刻投影（`_past_deadline`）的同一个 `delay`，以确保启用抖动时
        预算判定与真实退避一致——避免对退避时长做两次独立的随机采样。
        """
        if delay > 0:
            await self._sleep(delay)


def _reset_after(headers: Mapping[str, str]) -> float | None:
    value = headers.get("x-ogw-ratelimit-reset")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
