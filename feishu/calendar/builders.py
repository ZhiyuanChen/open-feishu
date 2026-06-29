# OpenFeishu
# Copyright (C) 2024-Present  DanLing

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from chanfig import NestedDict

DEFAULT_TIMEZONE = "Asia/Shanghai"


def calendar_time(value: Any, *, timezone: str = DEFAULT_TIMEZONE) -> NestedDict:
    r"""
    Build a Feishu calendar time object.

    Accepts an existing mapping, Unix seconds, a `date`, a `datetime`, an ISO date
    string (`YYYY-MM-DD`), or an ISO/RFC3339 datetime string.
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
    r"""Build a Feishu calendar event payload."""
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
    r"""Normalize attendee specs for `calendar.attendees.add`."""
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
    r"""Build a Feishu free/busy query body."""
    body = NestedDict(time_min=rfc3339(time_min, timezone=timezone), time_max=rfc3339(time_max, timezone=timezone))
    if user_id:
        body.user_id = user_id
    if room_id:
        body.room_id = room_id
    if not user_id and not room_id:
        raise ValueError("user_id or room_id is required")
    return body


def unix_seconds(value: Any, *, timezone: str = DEFAULT_TIMEZONE) -> int:
    r"""Convert common time values to Unix seconds."""
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
    r"""Convert common time values to an RFC3339 datetime string."""
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
