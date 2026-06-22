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

import logging
import re
from collections.abc import Iterable

_REDACTED = "***REDACTED***"


# Static secret patterns. Each substitutes the secret value with _REDACTED while
# keeping the surrounding key/prefix so log lines stay readable.
def _value_sub(m: re.Match) -> str:
    """Replacement helper for ``_PATTERNS`` subs: keep the captured prefix, redact the value."""
    return m.group(1) + _REDACTED


_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "Authorization: Bearer <token>" or bare "Bearer <token>".
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-+/=]+"),
    # tenant_access_token / app_access_token / user_access_token, rendered as
    # `"<key>": "<value>"` or `<key>=<value>`. The capture keeps the key plus the
    # separator/quote run; the value (letters, digits, . _ - + / =) is replaced.
    re.compile(r'([A-Za-z_]*access_token["\s:=]+["\']?)[A-Za-z0-9._\-+/=]+'),
    # app_secret value (same key/value shape).
    re.compile(r'(app_secret["\s:=]+["\']?)[A-Za-z0-9._\-+/=]+'),
    # client_secret value (OAuth token-grant body; same key/value shape).
    re.compile(r'(client_secret["\s:=]+["\']?)[A-Za-z0-9._\-+/=]+'),
    # encrypt_key value (same key/value shape).
    re.compile(r'(encrypt_key["\s:=]+["\']?)[A-Za-z0-9._\-+/=]+'),
)


class RedactingFilter(logging.Filter):
    r"""
    在日志输出前清除其中的飞书敏感信息。

    会将 Bearer 令牌、`*_access_token` / `app_secret` / `client_secret` /
    `encrypt_key` 等字段的取值，以及调用方额外提供的字面量密钥，统一替换为
    `***REDACTED***`。该过滤器从不丢弃日志记录（`filter` 始终返回 `True`），
    仅改写 `record.msg` 与 `record.args`。

    Args:
        secrets: 需要按字面量脱敏的额外密钥字符串集合（例如卡片更新令牌或已配置的
            `encrypt_key`）。

    Examples:
        >>> import logging
        >>> flt = RedactingFilter(secrets=["card-token-xyz"])
        >>> rec = logging.LogRecord("feishu", logging.INFO, __file__, 1,
        ...     '{"app_secret": "s3cret"} token=card-token-xyz', None, None)
        >>> flt.filter(rec)
        True
        >>> rec.getMessage()
        '{"app_secret": "***REDACTED***"} token=***REDACTED***'
    """

    def __init__(self, *, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        # Exact-literal secrets the app knows about (e.g. card-update tokens or a
        # configured encrypt_key). re.escape so regex metacharacters are literal.
        self._literals: tuple[re.Pattern[str], ...] = tuple(re.compile(re.escape(s)) for s in secrets if s)

    def _scrub(self, text: str) -> str:
        for pattern in _PATTERNS:
            text = pattern.sub(_value_sub, text)
        for literal in self._literals:
            text = literal.sub(_REDACTED, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: (self._scrub(v) if isinstance(v, str) else v) for k, v in record.args.items()}
            else:
                record.args = tuple(self._scrub(a) if isinstance(a, str) else a for a in record.args)
        return True


def install_redaction(logger_name: str = "feishu", *, secrets: Iterable[str] = ()) -> RedactingFilter:
    r"""
    幂等地为指定日志器安装脱敏过滤器并返回该过滤器。

    若目标日志器上已存在 [feishu._logging.RedactingFilter][]，则直接返回已有实例，
    不会重复添加，因此可安全地多次调用。

    Args:
        logger_name: 目标日志器名称，默认 `feishu`。
        secrets: 需要按字面量脱敏的额外密钥字符串集合。

    Returns:
        已安装（或复用的）[feishu._logging.RedactingFilter][] 实例。

    Examples:
        >>> first = install_redaction("feishu.demo")
        >>> second = install_redaction("feishu.demo")
        >>> first is second
        True
    """
    logger = logging.getLogger(logger_name)
    for existing in logger.filters:
        if isinstance(existing, RedactingFilter):
            return existing
    redactor = RedactingFilter(secrets=secrets)
    logger.addFilter(redactor)
    return redactor
