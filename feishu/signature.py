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

import hashlib
import hmac
import time
from collections.abc import Callable, Mapping


def verify_signature(timestamp: str, nonce: str, encrypt_key: str, raw_body: bytes, signature: str) -> bool:
    r"""
    校验飞书事件推送的签名。

    飞书在请求头中携带 `X-Lark-Request-Timestamp`、`X-Lark-Request-Nonce` 与 `X-Lark-Signature`。
    签名为 `sha256(timestamp + nonce + encrypt_key + raw_body)` 的十六进制摘要。
    本函数使用常量时间比较以避免计时攻击。

    Args:
        timestamp: 请求头 `X-Lark-Request-Timestamp` 的值。
        nonce: 请求头 `X-Lark-Request-Nonce` 的值。
        encrypt_key: 应用配置的 Encrypt Key。
        raw_body: HTTP 请求的原始字节体（必须在解析 JSON 之前读取，不能被改动）。
        signature: 请求头 `X-Lark-Signature` 的值。

    Returns:
        签名匹配返回 `True`，否则返回 `False`。

    飞书文档:
        [配置加密推送](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/encrypt-key-encryption-configuration-case)

    Examples:
        >>> import hashlib
        >>> ts, nonce, key, body = "1700000000", "abc123", "ek_secret", b'{"encrypt":"payload"}'
        >>> sig = hashlib.sha256((ts + nonce + key).encode("utf-8") + body).hexdigest()
        >>> verify_signature(ts, nonce, key, body, sig)
        True
        >>> verify_signature(ts, nonce, key, b'{"encrypt":"TAMPERED"}', sig)
        False
    """
    expected = hashlib.sha256((timestamp + nonce + encrypt_key).encode("utf-8") + raw_body).hexdigest()
    return hmac.compare_digest(expected, signature)


class SignatureVerifier:
    r"""
    可复用的飞书 Webhook 签名校验器，支持可选的重放时间窗保护。

    校验飞书事件/卡片回调请求的 `X-Lark-Signature` 签名，并可依据
    `X-Lark-Request-Timestamp` 拒绝过期请求以防重放。空的密钥会使 HMAC 退化为
    可伪造的固定哈希，因此构造时禁止传入空 `encrypt_key`。

    Args:
        encrypt_key: 应用配置的飞书 `encrypt_key`，不能为空字符串。
        max_age_seconds: 允许的请求最大时延（秒），默认 300；设为 `None` 则完全
            禁用重放时间窗校验（但绝不会跳过签名 MAC 校验）。
        now: 返回当前 epoch 时间（浮点秒）的可调用对象，默认 [time.time][]；
            可注入 lambda 以编写确定性测试。

    Raises:
        ValueError: 当 `encrypt_key` 为空字符串时抛出。

    飞书文档:
        [配置加密推送](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/encrypt-key-encryption-configuration-case)

    Examples:
        >>> import hashlib
        >>> ts, nonce, key, body = "1700000000", "n1", "ek_secret", b'{"type":"event"}'
        >>> sig = hashlib.sha256((ts + nonce + key).encode("utf-8") + body).hexdigest()
        >>> verifier = SignatureVerifier(key, now=lambda: float(ts))
        >>> verifier.is_valid(timestamp=ts, nonce=nonce, body=body, signature=sig)
        True
        >>> SignatureVerifier("")
        Traceback (most recent call last):
            ...
        ValueError: encrypt_key must be a non-empty string
    """

    def __init__(
        self, encrypt_key: str, *, max_age_seconds: float | None = 300, now: Callable[[], float] = time.time
    ) -> None:
        # A falsy key would reduce the HMAC to a fixed, forgeable hash (e.g. an
        # unset os.environ.get("ENCRYPT_KEY", "")), so refuse it outright.
        if not encrypt_key:
            raise ValueError("encrypt_key must be a non-empty string")
        self._encrypt_key = encrypt_key
        self._max_age_seconds = max_age_seconds
        self._now = now

    def is_valid(self, *, timestamp: str | None, nonce: str | None, body: bytes, signature: str | None) -> bool:
        r"""
        校验签名是否有效且请求未过期。

        任一字段缺失或为空字符串都会立即拒绝（部分网关会把缺失的请求头规整为空串）；
        启用重放时间窗时，时间戳无法解析或超出 `max_age_seconds` 也会拒绝；最后委托
        共享的加密函数完成 MAC 校验。

        Args:
            timestamp: 请求头 `X-Lark-Request-Timestamp` 的值（字符串或 `None`）。
            nonce: 请求头 `X-Lark-Request-Nonce` 的值（字符串或 `None`）。
            body: 原始请求体字节。
            signature: 请求头 `X-Lark-Signature` 的值（字符串或 `None`）。

        Returns:
            签名有效且请求未过期返回 `True`，否则返回 `False`。

        Examples:
            >>> import hashlib
            >>> ts, nonce, key, body = "1700000000", "n1", "ek_secret", b'{"type":"event"}'
            >>> sig = hashlib.sha256((ts + nonce + key).encode("utf-8") + body).hexdigest()
            >>> verifier = SignatureVerifier(key, now=lambda: float(ts))
            >>> verifier.is_valid(timestamp=ts, nonce=nonce, body=body, signature=sig)
            True
            >>> verifier.is_valid(timestamp=ts, nonce=nonce, body=b'{"type":"EVIL"}', signature=sig)
            False
            >>> verifier.is_valid(timestamp=None, nonce=nonce, body=body, signature=sig)
            False
        """
        # 1) Any missing OR EMPTY field → reject immediately. Some gateways/WSGI
        # layers normalize a missing header to "" rather than dropping it; an
        # empty timestamp/nonce would otherwise sign a fixed, predictable prefix.
        if not timestamp or not nonce or not signature:
            return False

        # 2) Replay-window check when enabled.
        if self._max_age_seconds is not None:
            try:
                ts_float = float(timestamp)
            except (ValueError, TypeError):
                return False
            if abs(self._now() - ts_float) > self._max_age_seconds:
                return False

        # 3) MAC verification — delegate to the shared crypto function.
        return verify_signature(str(timestamp), str(nonce), self._encrypt_key, body, signature)

    def is_valid_request(self, body: bytes, headers: Mapping[str, str]) -> bool:
        r"""
        校验由请求体与请求头构成的完整签名请求是否有效。

        从请求头中提取 `X-Lark-Signature`、`X-Lark-Request-Timestamp`、
        `X-Lark-Request-Nonce` 后委托 [feishu.signature.SignatureVerifier.is_valid][]
        校验。请求头名称按小写比较，故大小写不敏感。

        Args:
            body: 原始请求体字节。
            headers: 任意 `header 名 -> 值` 的映射。键会先转为小写再比较，因此
                `X-Lark-Signature`、`x-lark-signature`、`X-LARK-SIGNATURE` 均可识别。

        Returns:
            请求签名有效且未过期返回 `True`，否则返回 `False`。

        Examples:
            >>> import hashlib
            >>> ts, nonce, key, body = "1700000000", "n1", "ek_secret", b'{"schema":"2.0"}'
            >>> sig = hashlib.sha256((ts + nonce + key).encode("utf-8") + body).hexdigest()
            >>> headers = {
            ...     "X-Lark-Signature": sig,
            ...     "x-lark-request-timestamp": ts,
            ...     "X-LARK-REQUEST-NONCE": nonce,
            ... }
            >>> verifier = SignatureVerifier(key, now=lambda: float(ts))
            >>> verifier.is_valid_request(body, headers)
            True
            >>> verifier.is_valid_request(b'{"schema":"EVIL"}', headers)
            False
        """
        lowered = {k.lower(): v for k, v in headers.items()}
        signature = lowered.get("x-lark-signature")
        timestamp = lowered.get("x-lark-request-timestamp")
        nonce = lowered.get("x-lark-request-nonce")
        return self.is_valid(timestamp=timestamp, nonce=nonce, body=body, signature=signature)
