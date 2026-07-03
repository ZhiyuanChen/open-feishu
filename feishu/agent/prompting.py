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

import inspect
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


def build_time_aware_system_prompt(
    base_prompt: str,
    timezone_resolver: Callable[..., Any],
    *,
    now: Callable[[ZoneInfo], datetime] | None = None,
) -> Callable[..., Any]:
    r"""构建会追加当前日期、时间与时区上下文的 system prompt 提供方。"""
    prompt = base_prompt.rstrip()

    async def system(event: Any | None = None, timezone: str | None = None) -> str:
        tz = _valid_timezone(timezone) or await _call_timezone_resolver(timezone_resolver, event)
        zone = ZoneInfo(tz)
        current = now(zone) if now is not None else datetime.now(zone)
        return (
            f"{prompt}\n\n"
            f"Current datetime: {current.isoformat(timespec='seconds')}\n"
            f"Current date: {current.date().isoformat()}\n"
            f"Current timezone: {tz}"
        )

    return system


def build_timezone_resolver(
    default_timezone: str,
    *,
    user_tokens: Any | None = None,
    client: Any | None = None,
    cache_ttl_seconds: float = 3600.0,
) -> Callable[..., Any]:
    r"""构建按事件上下文、用户资料与配置默认值解析时区的 resolver。"""
    fallback_timezone = _valid_timezone(default_timezone) or "Asia/Shanghai"
    cache: dict[str, tuple[float, str]] = {}

    async def resolve(event: Any | None = None) -> str:
        event_timezone = _valid_timezone(_event_timezone(event))
        if event_timezone:
            return event_timezone
        user = _event_user(event)
        cache_key = _user_cache_key(user)
        if cache_key:
            cached = cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < cache_ttl_seconds:
                return cached[1]
        user_timezone = await _user_timezone(user_tokens, client, user)
        if user_timezone:
            if cache_key:
                cache[cache_key] = (time.monotonic(), user_timezone)
            return user_timezone
        return fallback_timezone

    return resolve


async def _call_timezone_resolver(timezone_resolver: Callable[..., Any], event: Any | None) -> str:
    value = timezone_resolver(event)
    if inspect.isawaitable(value):
        value = await value
    return _valid_timezone(value) or "Asia/Shanghai"


async def _user_timezone(user_tokens: Any | None, client: Any | None, user: Mapping[str, Any]) -> str | None:
    if not user or user_tokens is None or client is None:
        return None
    token_getter = getattr(user_tokens, "user_token", None)
    oauth = getattr(client, "oauth", None)
    user_info = getattr(oauth, "user_info", None)
    if token_getter is None or user_info is None:
        return None
    try:
        token = token_getter(user)
        if inspect.isawaitable(token):
            token = await token
        if not token:
            return None
        info = user_info(token)
        if inspect.isawaitable(info):
            info = await info
    except Exception:
        return None
    return _extract_timezone(info)


def _event_timezone(event: Any | None) -> str | None:
    body = getattr(event, "body", None) or {}
    nodes = [
        body,
        body.get("context") or {},
        body.get("action") or {},
        (body.get("action") or {}).get("option") or {},
    ]
    for node in nodes:
        timezone = _extract_timezone(node)
        if timezone:
            return timezone
    return None


def _event_user(event: Any | None) -> dict[str, Any]:
    body = getattr(event, "body", None) or {}
    sender = (body.get("sender") or {}).get("sender_id") or {}
    source = sender or body.get("operator") or {}
    return {key: source[key] for key in ("open_id", "union_id", "user_id") if source.get(key)}


def _user_cache_key(user: Mapping[str, Any]) -> str | None:
    for key in ("open_id", "union_id", "user_id"):
        value = user.get(key)
        if value:
            return f"{key}:{value}"
    return None


def _extract_timezone(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("timezone", "time_zone", "timeZone", "tz"):
        timezone = _valid_timezone(value.get(key))
        if timezone:
            return timezone
    for key in ("user", "data", "profile"):
        timezone = _extract_timezone(value.get(key))
        if timezone:
            return timezone
    return None


def _valid_timezone(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    timezone = value.strip()
    try:
        ZoneInfo(timezone)
    except Exception:
        return None
    return timezone
