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

from typing import Any

from chanfig import NestedDict

from .._namespace import Namespace
from .._url import quote_segment


class EventsNamespace(Namespace):
    r"""
    日程接口命名空间。

    通过 `client.calendar.events` 访问，封装飞书日历中日程（event）相关的服务端接口，包括日程的创建、
    查询、更新、删除与列举等能力。日程隶属于某个日历，以 `event_id` 标识。

    通常无需直接实例化，应通过 `client.calendar.events` 访问。

    飞书文档:
        [日程](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event/create)
    """

    async def create(
        self,
        calendar_id: str,
        event: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        user_id_type: str | None = None,
    ) -> NestedDict:
        r"""
        创建日程。

        `event` 是描述待创建日程的请求体，原样作为 JSON 发送，常见键包括 `summary`、
        `description`、`start_time`、`end_time`、`vchat`、`visibility`、`reminders` 等。
        仅当显式传入 `idempotency_key` 时才将其并入查询参数，用于幂等创建。

        Args:
            calendar_id: 日历的 `calendar_id`。
            event: 日程定义对象，例如
                `{"summary": "周会", "start_time": {...}, "end_time": {...}}`。
            idempotency_key: 幂等键；设置后在一段时间内重复请求只会创建一个日程，
                为空时省略该参数。
            user_id_type: 日程相关用户字段的 ID 类型；为空时使用飞书接口默认值。

        Returns:
            包含 `event` 字段的数据，`event` 内含新建日程的 `event_id`、`summary`、
            `start_time`、`end_time` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [创建日程](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event/create)

        Examples:
            >>> await client.calendar.events.create("feishu.cn_xxx", {"summary": "周会"})  # doctest:+SKIP
            {'event': {'event_id': 'xxx', 'summary': '周会', ...}}  # noqa: E501
        """
        params = {}
        if idempotency_key is not None:
            params["idempotency_key"] = idempotency_key
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        return await self._request_data(
            "POST", f"calendar/v4/calendars/{quote_segment(calendar_id)}/events", params=params, json=event
        )

    async def delete(self, calendar_id: str, event_id: str) -> NestedDict:
        r"""
        删除日程。

        Args:
            calendar_id: 日历的 `calendar_id`。
            event_id: 待删除日程的 `event_id`。

        Returns:
            接口返回的数据（通常为空）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [删除日程](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event/delete)

        Examples:
            >>> await client.calendar.events.delete("feishu.cn_xxx", "evtxxx")  # doctest:+SKIP
            {}
        """
        return await self._request_data(
            "DELETE",
            f"calendar/v4/calendars/{quote_segment(calendar_id)}/events/{quote_segment(event_id)}",
        )

    async def get(self, calendar_id: str, event_id: str, *, user_id_type: str | None = None) -> NestedDict:
        r"""
        获取日程信息。

        Args:
            calendar_id: 日历的 `calendar_id`。
            event_id: 日程的 `event_id`。
            user_id_type: 日程相关用户字段的 ID 类型；为空时使用飞书接口默认值。

        Returns:
            包含 `event` 字段的数据，`event` 内含 `event_id`、`summary`、`description`、
            `start_time`、`end_time` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取日程](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event/get)

        Examples:
            >>> await client.calendar.events.get("feishu.cn_xxx", "evtxxx")  # doctest:+SKIP
            {'event': {'event_id': 'evtxxx', 'summary': '周会', ...}}  # noqa: E501
        """
        return await self._request_data(
            "GET",
            f"calendar/v4/calendars/{quote_segment(calendar_id)}/events/{quote_segment(event_id)}",
            params={"user_id_type": user_id_type} if user_id_type is not None else None,
        )

    async def list(
        self,
        calendar_id: str,
        *,
        page_size: int = 50,
        max_items: int | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> list[NestedDict]:
        r"""
        获取日程列表。

        自动翻页并将各页结果拼接为单个列表返回。`page_size` 会被限制在
        [feishu.consts.MAX_PAGE_SIZE][] 以内。仅将显式传入的可选查询参数并入请求，
        未设置的项会被省略。

        Args:
            calendar_id: 日历的 `calendar_id`。
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。
            start_time: 时间范围的起始（Unix 秒时间戳字符串）；为空时省略该参数。
            end_time: 时间范围的结束（Unix 秒时间戳字符串）；为空时省略该参数。

        Returns:
            日程数据列表，每项包含 `event_id`、`summary`、`start_time`、`end_time` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取日程列表](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event/list)

        Examples:
            >>> await client.calendar.events.list("feishu.cn_xxx")  # doctest:+SKIP
            [{'event_id': 'evtxxx', 'summary': '周会', ...}, ...]  # noqa: E501
        """
        params: dict[str, Any] = {}
        if start_time is not None:
            params["start_time"] = start_time
        if end_time is not None:
            params["end_time"] = end_time
        return await self._client.paginate_get(
            f"calendar/v4/calendars/{quote_segment(calendar_id)}/events",
            params=params,
            page_size=page_size,
            max_items=max_items,
        )

    async def update(
        self, calendar_id: str, event_id: str, event: dict[str, Any], *, user_id_type: str | None = None
    ) -> NestedDict:
        r"""
        更新日程。

        `event` 是描述待更新字段的请求体，原样作为 JSON 发送；仅传入的字段会被更新，
        未传入的字段保持不变，常见键包括 `summary`、`description`、`start_time`、`end_time`
        等。

        Args:
            calendar_id: 日历的 `calendar_id`。
            event_id: 待更新日程的 `event_id`。
            event: 待更新字段的映射，例如 `{"summary": "已改期的周会"}`。
            user_id_type: 日程相关用户字段的 ID 类型；为空时使用飞书接口默认值。

        Returns:
            包含 `event` 字段的数据，`event` 内含更新后日程的 `event_id`、`summary` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [更新日程](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event/patch)

        Examples:
            >>> await client.calendar.events.update("feishu.cn_xxx", "evtxxx", {"summary": "改期"})  # doctest:+SKIP
            {'event': {'event_id': 'evtxxx', 'summary': '改期', ...}}  # noqa: E501
        """
        return await self._request_data(
            "PATCH",
            f"calendar/v4/calendars/{quote_segment(calendar_id)}/events/{quote_segment(event_id)}",
            params={"user_id_type": user_id_type} if user_id_type is not None else None,
            json=event,
        )

    async def reply(self, calendar_id: str, event_id: str, *, rsvp_status: str) -> NestedDict:
        r"""
        回复（RSVP）日程邀请。

        将当前身份对某个日程邀请的出席意向设置为 `rsvp_status`：`"accept"` 接受、`"tentative"` 待定、
        `"decline"` 拒绝。

        Args:
            calendar_id: 日历的 `calendar_id`。
            event_id: 日程的 `event_id`。
            rsvp_status: 出席意向，`"accept"`/`"tentative"`/`"decline"` 之一。

        Returns:
            接口返回的数据（通常为空）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [回复日程](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/calendar-v4/calendar-event/reply)

        Examples:
            >>> await client.calendar.events.reply("feishu.cn_xxx", "evtxxx", rsvp_status="accept")  # doctest:+SKIP
            {}
        """
        return await self._request_data(
            "POST",
            f"calendar/v4/calendars/{quote_segment(calendar_id)}/events/{quote_segment(event_id)}/reply",
            json={"rsvp_status": rsvp_status},
        )
