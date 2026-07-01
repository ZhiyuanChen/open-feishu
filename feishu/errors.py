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

from collections.abc import Mapping
from typing import Any

RATE_LIMIT_CODES = {99991400}
SERVER_RETRY_STATUS = {500, 502, 503, 504}

# Feishu returns these in the JSON envelope's ``code`` (often with HTTP 200) to
# signal an invalid/missing/expired token or credential -> an auth failure.
AUTH_ERROR_CODES = {
    99991661,  # missing Authorization token
    99991663,  # invalid/expired tenant_access_token (also wrong token type)
    99991664,  # malformed app_access_token
    99991665,  # invalid tenant_access_token format
    99991668,  # expired user_access_token
    99991669,  # invalid user refresh token
    99991670,  # invalid SSO access token
    99991671,  # token format must start with "t-"/"u-"
    99991677,  # user_access_token expired (variant)
}
# The app/user lacks a required permission scope (grant it in the Developer
# Console, or have the user re-consent) -- distinct from a bad token.
PERMISSION_ERROR_CODES = {
    99991672,  # app missing an API permission scope
    99991676,  # token lacks required permissions
    99991679,  # user authorization missing scope
}


class FeishuError(Exception):
    r"""
    所有飞书异常的基类。

    封装飞书 API 返回的错误码、错误信息以及用于排查问题的链路追踪 ID（log_id），
    同时保留原始响应体（raw）以便进一步分析。所有具体的异常类型都继承自该类，
    因此 `except FeishuError` 可以捕获本库抛出的全部飞书相关异常。

    Args:
        code: 飞书业务错误码，传输层错误固定为 `-1`。
        message: 人类可读的错误信息。
        log_id: 飞书返回的链路追踪 ID，用于向飞书反馈问题时定位日志。
        raw: 原始响应体（通常为 `dict`），便于排查未被归类的字段。

    飞书文档:
        [通用错误码](https://open.feishu.cn/document/server-docs/api-call-guide/generic-error-code)

    Examples:
        >>> err = FeishuError(99991663, "bad token", log_id="lg-1", raw={"x": 1})
        >>> err.code
        99991663
        >>> err.log_id
        'lg-1'
        >>> str(err)
        "FeishuError(code=99991663, message='bad token', log_id='lg-1')"
    """

    def __init__(self, code: int, message: str, *, log_id: str | None = None, raw: Any = None):  # noqa: B042
        super().__init__(message)
        self.code = code
        self.message = message
        self.log_id = log_id
        self.raw = raw

    def __str__(self) -> str:
        return f"{type(self).__name__}(code={self.code}, message={self.message!r}, log_id={self.log_id!r})"


class FeishuAuthError(FeishuError):
    r"""
    鉴权失败异常。

    当令牌缺失、无效、过期或类型错误时抛出（例如 `tenant_access_token` 过期，
    或 OAuth 授权码已失效）。一般通过刷新或重新获取令牌即可恢复。

    飞书文档:
        [通用错误码](https://open.feishu.cn/document/server-docs/api-call-guide/generic-error-code)

    Examples:
        >>> err = FeishuAuthError(99991663, "invalid token")
        >>> isinstance(err, FeishuError)
        True
    """


class FeishuPermissionError(FeishuAuthError):
    r"""
    权限不足异常。

    当应用或用户缺少接口所需的权限范围（scope）时抛出。这属于配置或授权问题
    （需在开发者后台为应用申请权限，或引导用户重新授权），与令牌无效或过期不同。
    该类继承自 [feishu.errors.FeishuAuthError][]，因此 `except FeishuAuthError`
    也能捕获权限错误。

    飞书文档:
        [通用错误码](https://open.feishu.cn/document/server-docs/api-call-guide/generic-error-code)

    Examples:
        >>> err = FeishuPermissionError(99991672, "no scope")
        >>> isinstance(err, FeishuAuthError)
        True
    """


def permission_subjects(error: Exception) -> tuple[str, ...]:
    r"""
    从飞书权限错误中提取缺失的权限主体。

    飞书权限失败可能把 ``permission_violations`` 嵌在 ``message`` 或原始响应信封中。
    本函数按发现顺序返回去重后的 ``subject`` 值。
    """
    subjects: list[str] = []
    for value in (getattr(error, "message", None), getattr(error, "raw", None)):
        _collect_permission_subjects(value, subjects)
    return tuple(dict.fromkeys(subjects))


def is_permission_error(error: Exception) -> bool:
    r"""
    判断异常是否表示飞书权限范围失败。
    """
    if isinstance(error, FeishuPermissionError):
        return True
    if permission_subjects(error):
        return True
    code = getattr(error, "code", None)
    return code in PERMISSION_ERROR_CODES


def _collect_permission_subjects(value: Any, subjects: list[str]) -> None:
    if isinstance(value, Mapping):
        violations = value.get("permission_violations")
        if isinstance(violations, list):
            for violation in violations:
                if isinstance(violation, Mapping):
                    subject = violation.get("subject")
                    if isinstance(subject, str) and subject:
                        subjects.append(subject)
        for item in value.values():
            _collect_permission_subjects(item, subjects)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _collect_permission_subjects(item, subjects)


class FeishuApiError(FeishuError):
    r"""
    通用业务错误异常。

    当请求未命中限流、服务器错误或鉴权错误等特定分类，而是返回了普通的业务错误码
    （例如参数非法、资源不存在、被拒绝）时抛出。

    飞书文档:
        [通用错误码](https://open.feishu.cn/document/server-docs/api-call-guide/generic-error-code)

    Examples:
        >>> err = FeishuApiError(230002, "denied")
        >>> err.code
        230002
    """


class FeishuServerError(FeishuError):
    r"""
    飞书服务器错误异常。

    对应 HTTP 500/502/503/504。这类错误通常是临时性的，传输层会自动重试；
    若重试耗尽仍未恢复，则向调用方抛出该异常。

    飞书文档:
        [通用错误码](https://open.feishu.cn/document/server-docs/api-call-guide/generic-error-code)

    Examples:
        >>> err = FeishuServerError(0, "unavailable")
        >>> isinstance(err, FeishuError)
        True
    """


class FeishuRateLimitError(FeishuError):
    r"""
    触发限流异常。

    对应 HTTP 429 或限流错误码。传输层会先按 `reset_after`（若服务器提供）
    自动退避重试；重试耗尽后向调用方抛出该异常。`reset_after` 表示建议的
    重试等待秒数。

    Args:
        code: 飞书业务错误码。
        message: 人类可读的错误信息。
        log_id: 飞书返回的链路追踪 ID。
        raw: 原始响应体。
        reset_after: 建议的重试等待秒数，来源于 `x-ogw-ratelimit-reset` 响应头。

    飞书文档:
        [服务端 API 调用频率限制](https://open.feishu.cn/document/server-docs/api-call-guide/frequency-control)

    Examples:
        >>> err = FeishuRateLimitError(99991400, "slow", reset_after=2.0)
        >>> err.reset_after
        2.0
    """

    def __init__(  # noqa: B042
        self,
        code: int,
        message: str,
        *,
        log_id: str | None = None,
        raw: Any = None,
        reset_after: float | None = None,
    ) -> None:
        super().__init__(code, message, log_id=log_id, raw=raw)
        self.reset_after = reset_after


class FeishuTransportError(FeishuError):
    r"""
    传输层错误异常。

    当 HTTP 请求本身失败（连接错误、超时等网络问题）且重试耗尽时抛出。
    错误码固定为 `-1`，底层原始异常保留在 `original` 属性中以便排查。

    Args:
        message: 人类可读的错误信息。
        original: 触发该错误的底层异常（通常为 `httpx.RequestError`）。

    Examples:
        >>> cause = ValueError("boom")
        >>> err = FeishuTransportError("request failed: boom", original=cause)
        >>> err.code
        -1
        >>> err.original is cause
        True
    """

    def __init__(self, message: str, *, original: BaseException | None = None):  # noqa: B042
        super().__init__(-1, message)
        self.original = original


class FeishuSignatureError(FeishuError):
    r"""
    Webhook 签名校验失败异常。

    供需要严格的、基于抛异常的签名校验的调用方使用。注意：本库内置的 Starlette
    接收器（`create_event_route` / `create_card_route`）在签名校验失败时返回
    HTTP 401 响应，而不会抛出该异常。

    飞书文档:
        [配置订阅方式-加密](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case)

    Examples:
        >>> err = FeishuSignatureError(401, "signature mismatch")
        >>> isinstance(err, FeishuError)
        True
    """


class FeishuCryptoError(FeishuError):
    r"""
    事件解密失败异常。

    当 `feishu.events.crypto.decrypt` 无法解密密文时抛出，例如加密密钥错误、
    密文损坏或 PKCS#7 填充非法。

    飞书文档:
        [事件订阅概述-数据加密](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/encrypt-key-encryption-configuration-case)

    Examples:
        >>> err = FeishuCryptoError(-1, "bad padding", raw={"encrypt": "x"})
        >>> err.code
        -1
    """


def error_from_envelope(
    code: int,
    message: str,
    *,
    status: int,
    log_id: str | None,
    raw: Any,
    reset_after: float | None = None,
    error_description: str | None = None,
) -> FeishuError:
    r"""
    依据 HTTP 状态码与响应体错误码，构造对应的具体异常实例。

    分类顺序为：限流（429 或限流码）> 服务器错误（5xx）> 权限不足 > 鉴权失败
    （鉴权错误码、401/403，或 OAuth `invalid_*` 等错误形态）> 通用业务错误。
    错误描述优先取 `error_description`，其次取响应体中的 `error_description` /
    `error`，最后回退到 `message`。

    Args:
        code: 响应体中的业务错误码。
        message: 响应体中的 `msg` 字段。
        status: HTTP 状态码。
        log_id: 飞书返回的链路追踪 ID。
        raw: 原始响应体。
        reset_after: 建议的重试等待秒数（用于限流错误）。
        error_description: OAuth 风格响应中的错误描述，优先作为错误信息。

    Returns:
        与状态码、错误码相匹配的 [feishu.errors.FeishuError][] 子类实例。

    Examples:
        >>> err = error_from_envelope(99991400, "slow", status=429, log_id=None, raw={})
        >>> type(err).__name__
        'FeishuRateLimitError'
        >>> err = error_from_envelope(0, "unavailable", status=503, log_id=None, raw={})
        >>> type(err).__name__
        'FeishuServerError'
        >>> err = error_from_envelope(99991672, "no scope", status=200, log_id=None, raw={})
        >>> type(err).__name__
        'FeishuPermissionError'
        >>> err = error_from_envelope(99991663, "bad token", status=200, log_id=None, raw={})
        >>> type(err).__name__
        'FeishuAuthError'
        >>> err = error_from_envelope(230002, "denied", status=200, log_id=None, raw={})
        >>> type(err).__name__
        'FeishuApiError'
    """
    text = error_description
    if not text and isinstance(raw, dict):
        text = raw.get("error_description") or raw.get("error")
    if not text:
        text = message or ""
    if code in RATE_LIMIT_CODES or status == 429:
        return FeishuRateLimitError(code, text, log_id=log_id, raw=raw, reset_after=reset_after)
    if status in SERVER_RETRY_STATUS:
        return FeishuServerError(code, text, log_id=log_id, raw=raw)
    if code in PERMISSION_ERROR_CODES:
        return FeishuPermissionError(code, text, log_id=log_id, raw=raw)
    if code in AUTH_ERROR_CODES or status in (401, 403) or (isinstance(raw, dict) and _is_auth_error(raw)):
        return FeishuAuthError(code, text, log_id=log_id, raw=raw)
    return FeishuApiError(code, text, log_id=log_id, raw=raw)


def _is_auth_error(raw: dict[str, Any]) -> bool:
    err = raw.get("error")
    if not isinstance(err, str):
        return False
    return err.startswith("invalid_") or err in ("unauthorized_client", "access_denied", "unauthorized")
