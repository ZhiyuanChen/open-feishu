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

from typing import TYPE_CHECKING

from .._namespace import Namespace

if TYPE_CHECKING:
    from .attendees import AttendeesNamespace
    from .calendars import CalendarsNamespace
    from .events import EventsNamespace
    from .freebusy import FreebusyNamespace


class CalendarNamespace(Namespace):
    r"""
    日历（Calendar）接口命名空间。

    通过 `client.calendar` 访问，作为日历、日程、参与人与忙闲四个子命名空间的入口：
    [`CalendarNamespace.attendees`][feishu.calendar.calendar.CalendarNamespace.attendees]
    暴露日程参与人相关能力，
    [`CalendarNamespace.calendars`][feishu.calendar.calendar.CalendarNamespace.calendars]
    暴露日历相关能力，
    [`CalendarNamespace.events`][feishu.calendar.calendar.CalendarNamespace.events]
    暴露日程相关能力，
    [`CalendarNamespace.freebusy`][feishu.calendar.calendar.CalendarNamespace.freebusy]
    暴露忙闲查询能力。各子命名空间均在首次访问时惰性创建。日历以 `calendar_id` 标识，
    日历内含若干日程，每个日程以 `event_id` 标识、可包含多名参与人。

    通常无需直接实例化，应通过 `client.calendar` 访问。

    飞书文档:
        [日历概述](https://open.feishu.cn/document/server-docs/calendar-v4/calendar/introduction)
    """

    _attendees: AttendeesNamespace | None = None
    _calendars: CalendarsNamespace | None = None
    _events: EventsNamespace | None = None
    _freebusy: FreebusyNamespace | None = None

    @property
    def attendees(self) -> AttendeesNamespace:
        r"""
        日程参与人接口命名空间。

        惰性创建并返回 [feishu.calendar.attendees.AttendeesNamespace][]，用于添加、列举与删除日程参与人。

        Returns:
            日程参与人接口命名空间实例。

        飞书文档:
            [日程参与人](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event-attendee/create)

        Examples:
            >>> client.calendar.attendees  # doctest:+SKIP
            <feishu.calendar.attendees.AttendeesNamespace object at ...>
        """
        if self._attendees is None:
            from .attendees import AttendeesNamespace

            self._attendees = AttendeesNamespace(self._client)
        return self._attendees

    @property
    def calendars(self) -> CalendarsNamespace:
        r"""
        日历接口命名空间。

        惰性创建并返回 [feishu.calendar.calendars.CalendarsNamespace][]，用于创建、查询、更新、删除与列举日历，
        以及查询主日历。

        Returns:
            日历接口命名空间实例。

        飞书文档:
            [日历](https://open.feishu.cn/document/server-docs/calendar-v4/calendar/create)

        Examples:
            >>> client.calendar.calendars  # doctest:+SKIP
            <feishu.calendar.calendars.CalendarsNamespace object at ...>
        """
        if self._calendars is None:
            from .calendars import CalendarsNamespace

            self._calendars = CalendarsNamespace(self._client)
        return self._calendars

    @property
    def events(self) -> EventsNamespace:
        r"""
        日程接口命名空间。

        惰性创建并返回 [feishu.calendar.events.EventsNamespace][]，用于创建、查询、更新、删除与列举日程。

        Returns:
            日程接口命名空间实例。

        飞书文档:
            [日程](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event/create)

        Examples:
            >>> client.calendar.events  # doctest:+SKIP
            <feishu.calendar.events.EventsNamespace object at ...>
        """
        if self._events is None:
            from .events import EventsNamespace

            self._events = EventsNamespace(self._client)
        return self._events

    @property
    def freebusy(self) -> FreebusyNamespace:
        r"""
        忙闲接口命名空间。

        惰性创建并返回 [feishu.calendar.freebusy.FreebusyNamespace][]，用于查询主日历忙闲信息。

        Returns:
            忙闲接口命名空间实例。

        飞书文档:
            [查询主日历忙闲信息](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/calendar-v4/freebusy/list)

        Examples:
            >>> client.calendar.freebusy  # doctest:+SKIP
            <feishu.calendar.freebusy.FreebusyNamespace object at ...>
        """
        if self._freebusy is None:
            from .freebusy import FreebusyNamespace

            self._freebusy = FreebusyNamespace(self._client)
        return self._freebusy
