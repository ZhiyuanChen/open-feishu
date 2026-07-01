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

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from chanfig import NestedDict

DEFAULT_TIMEZONE = "Asia/Shanghai"


def calendar_time(value: Any, *, timezone: str = DEFAULT_TIMEZONE) -> NestedDict:
    r"""
    构造飞书日历时间对象。

    支持传入已有映射、Unix 秒级时间戳、[datetime.date][]、[datetime.datetime][]、
    ISO 日期字符串（`YYYY-MM-DD`），或 ISO/RFC3339 日期时间字符串。

    Args:
        value: 待转换的时间取值，可为映射、`int`/`float` 时间戳、[datetime.date][]、
            [datetime.datetime][]，或日期/日期时间字符串。
        timezone: 用于解析无时区信息取值的时区，默认为 `Asia/Shanghai`；纯日期取值不携带该字段。

    Returns:
        飞书日历时间对象，纯日期取值含 `date` 字段，其余取值含 `timestamp` 与 `timezone` 字段。

    Raises:
        ValueError: 传入空字符串时抛出。
        TypeError: 传入不受支持的取值类型时抛出。
    """
    if isinstance(value, NestedDict):
        return value
    if isinstance(value, Mapping):
        return NestedDict(value)
    if isinstance(value, datetime):
        return NestedDict(timestamp=str(int(_aware_datetime(value, timezone).timestamp())), timezone=timezone)
    if isinstance(value, date):
        return NestedDict(date=value.isoformat())
    if isinstance(value, (int, float)):
        return NestedDict(timestamp=str(int(value)), timezone=timezone)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("calendar time cannot be empty")
        if text.isdigit():
            return NestedDict(timestamp=text, timezone=timezone)
        if _is_iso_date(text):
            return NestedDict(date=text)
        return NestedDict(timestamp=str(unix_seconds(text, timezone=timezone)), timezone=timezone)
    raise TypeError(f"unsupported calendar time value: {type(value)!r}")


def calendar_event(
    *,
    summary: str,
    start_time: Any,
    end_time: Any,
    timezone: str = DEFAULT_TIMEZONE,
    description: str | None = None,
    location: str | None = None,
    visibility: str | None = None,
    free_busy_status: str | None = "busy",
    reminders: Sequence[Mapping[str, Any]] | None = None,
    vchat: Mapping[str, Any] | None = None,
) -> NestedDict:
    r"""
    构造可传给 [feishu.calendar.events.EventsNamespace.create][] 的飞书日历日程载荷。

    `start_time` 与 `end_time` 会经 [calendar_time][feishu.calendar.builders.calendar_time] 归一化。
    仅当对应可选参数为真值时才并入载荷，未设置的项会被省略。

    Args:
        summary: 日程标题。
        start_time: 日程开始时间，取值同 [calendar_time][feishu.calendar.builders.calendar_time]。
        end_time: 日程结束时间，取值同 [calendar_time][feishu.calendar.builders.calendar_time]。
        timezone: 用于解析开始/结束时间的时区，默认为 `Asia/Shanghai`。
        description: 日程描述；为空时省略该字段。
        location: 日程地点名称，会包装为 `{"name": ...}`；为空时省略该字段。
        visibility: 日程可见性（如 `default`/`public`/`private`）；为空时省略该字段。
        free_busy_status: 日程忙闲状态，默认为 `"busy"`（亦可为 `"free"`）；为假值时省略该字段。
        reminders: 提醒项列表，每项会包装为 [chanfig.NestedDict][]；为空时省略该字段。
        vchat: 视频会议配置，会包装为 [chanfig.NestedDict][]；为空时省略该字段。

    Returns:
        日程载荷 [chanfig.NestedDict][]，含 `summary`、`start_time`、`end_time` 及按需并入的可选字段。
    """
    event = NestedDict(
        summary=summary,
        start_time=calendar_time(start_time, timezone=timezone),
        end_time=calendar_time(end_time, timezone=timezone),
    )
    if description:
        event.description = description
    if location:
        event.location = NestedDict(name=location)
    if visibility:
        event.visibility = visibility
    if free_busy_status:
        event.free_busy_status = free_busy_status
    if reminders:
        event.reminders = [NestedDict(reminder) for reminder in reminders]
    if vchat:
        event.vchat = NestedDict(vchat)
    return event


def calendar_attendees(values: Sequence[Any] | None) -> list[NestedDict]:
    r"""
    归一化日程参与人列表，供 [feishu.calendar.attendees.AttendeesNamespace.add][] 使用。

    当调用方未显式传入 `type` 时，会按字段推断参与人类型：含 `room_id` 为会议室资源，
    含 `chat_id` 为群组，含 `third_party_email` 为外部邮箱，否则按用户处理。

    Args:
        values: 参与人映射序列，每项常见键包括 `type`、`user_id`、`chat_id`、`room_id`、
            `third_party_email` 等；为空时返回空列表。

    Returns:
        归一化后的参与人 [chanfig.NestedDict][] 列表，每项均带有推断或显式的 `type` 字段。

    Raises:
        TypeError: 列表中存在非映射类型的参与人取值时抛出。
    """
    if not values:
        return []
    attendees: list[NestedDict] = []
    for value in values:
        if isinstance(value, NestedDict):
            attendee = value
        elif isinstance(value, Mapping):
            attendee = NestedDict(value)
        else:
            raise TypeError(f"unsupported attendee value: {type(value)!r}")
        if "type" not in attendee:
            if attendee.get("room_id"):
                attendee.type = "resource"
            elif attendee.get("chat_id"):
                attendee.type = "chat"
            elif attendee.get("third_party_email"):
                attendee.type = "third_party"
            else:
                attendee.type = "user"
        attendees.append(attendee)
    return attendees


def freebusy_body(
    *,
    time_min: Any,
    time_max: Any,
    user_id: str | None = None,
    room_id: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> NestedDict:
    r"""构造飞书忙闲查询请求体，供 [feishu.calendar.freebusy.FreebusyNamespace.query][] 使用。"""
    body = NestedDict(time_min=rfc3339(time_min, timezone=timezone), time_max=rfc3339(time_max, timezone=timezone))
    if user_id:
        body.user_id = user_id
    if room_id:
        body.room_id = room_id
    if not user_id and not room_id:
        raise ValueError("user_id or room_id is required")
    return body


def unix_seconds(value: Any, *, timezone: str = DEFAULT_TIMEZONE) -> int:
    r"""将常见时间取值转换为 Unix 秒级时间戳。"""
    if isinstance(value, datetime):
        return int(_aware_datetime(value, timezone).timestamp())
    if isinstance(value, date):
        start = datetime(value.year, value.month, value.day, tzinfo=ZoneInfo(timezone))
        return int(start.timestamp())
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("time value cannot be empty")
        if text.isdigit():
            return int(text)
        return int(_parse_datetime(text, timezone).timestamp())
    if isinstance(value, Mapping):
        if value.get("timestamp"):
            return int(value["timestamp"])
        if value.get("date"):
            return unix_seconds(str(value["date"]), timezone=timezone)
        if value.get("date_time"):
            return unix_seconds(str(value["date_time"]), timezone=timezone)
    raise TypeError(f"unsupported time value: {type(value)!r}")


def rfc3339(value: Any, *, timezone: str = DEFAULT_TIMEZONE) -> str:
    r"""将常见时间取值转换为 RFC3339 日期时间字符串。"""
    if isinstance(value, str) and not value.strip().isdigit() and not _is_iso_date(value.strip()):
        return _parse_datetime(value.strip(), timezone).isoformat()
    dt = datetime.fromtimestamp(unix_seconds(value, timezone=timezone), tz=ZoneInfo(timezone))
    return dt.isoformat()


def _aware_datetime(value: datetime, timezone: str) -> datetime:
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=ZoneInfo(timezone))


def _parse_datetime(value: str, timezone: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    if _is_iso_date(normalized):
        parsed = datetime.fromisoformat(normalized)
        return parsed.replace(tzinfo=ZoneInfo(timezone))
    parsed = datetime.fromisoformat(normalized)
    return _aware_datetime(parsed, timezone)


def _is_iso_date(value: str) -> bool:
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True
