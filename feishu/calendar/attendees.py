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


class AttendeesNamespace(Namespace):
    r"""
    日程参与人接口命名空间。

    通过 `client.calendar.attendees` 访问，封装飞书日历中日程参与人（attendee）相关的服务端接口，
    包括参与人的添加、列举与删除等能力。参与人隶属于某个日程，以 `attendee_id` 标识。

    通常无需直接实例化，应通过 `client.calendar.attendees` 访问。

    飞书文档:
        [日程参与人](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event-attendee/create)
    """

    async def add(
        self,
        calendar_id: str,
        event_id: str,
        attendees: list[dict[str, Any]],
        *,
        need_notification: bool | None = None,
    ) -> NestedDict:
        r"""
        添加日程参与人。

        `attendees` 是待添加的参与人列表，作为请求体的 `attendees` 字段发送，每个元素
        常见键包括 `type`、`user_id`、`chat_id`、`room_id`、`third_party_email` 等。
        仅当显式传入 `need_notification` 时才将其并入请求体的 `need_notification` 字段。

        Args:
            calendar_id: 日历的 `calendar_id`。
            event_id: 日程的 `event_id`。
            attendees: 待添加的参与人列表，例如 `[{"type": "user", "user_id": "ou_xxx"}]`。
            need_notification: 是否在添加后发送 Bot 通知；为空时省略该字段。

        Returns:
            包含 `attendees` 字段的数据，`attendees` 为新增参与人列表（每项含
            `attendee_id`、`type` 等字段）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [添加日程参与人](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event-attendee/create)

        Examples:
            >>> atts = [{"type": "user", "user_id": "ou_xxx"}]
            >>> await client.calendar.attendees.add("feishu.cn_xxx", "evtxxx", atts)  # doctest:+SKIP
            {'attendees': [{'attendee_id': 'axxx', 'type': 'user', ...}]}  # noqa: E501
        """
        body: dict[str, Any] = {"attendees": attendees}
        if need_notification is not None:
            body["need_notification"] = need_notification
        return await self._request_data(
            "POST",
            f"calendar/v4/calendars/{quote_segment(calendar_id)}/events/{quote_segment(event_id)}/attendees",
            json=body,
        )

    async def delete(self, calendar_id: str, event_id: str, attendee_ids: list[str]) -> NestedDict:
        r"""
        删除日程参与人。

        `attendee_ids` 为待删除参与人的 ID 列表，原样作为请求体的 `attendee_ids` 字段发送。

        Args:
            calendar_id: 日历的 `calendar_id`。
            event_id: 日程的 `event_id`。
            attendee_ids: 待删除参与人的 `attendee_id` 列表。

        Returns:
            接口返回的数据（通常为空）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [删除日程参与人](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event-attendee/batch_delete)

        Examples:
            >>> await client.calendar.attendees.delete("feishu.cn_xxx", "evtxxx", ["a1", "a2"])  # doctest:+SKIP
            {}
        """
        return await self._request_data(
            "POST",
            f"calendar/v4/calendars/{quote_segment(calendar_id)}/events/{quote_segment(event_id)}"
            f"/attendees/batch_delete",
            json={"attendee_ids": attendee_ids},
        )

    async def list(
        self, calendar_id: str, event_id: str, *, page_size: int = 50, max_items: int | None = None
    ) -> list[NestedDict]:
        r"""
        获取日程参与人列表。

        自动翻页并将各页结果拼接为单个列表返回。`page_size` 会被限制在
        [feishu.consts.MAX_PAGE_SIZE][] 以内。

        Args:
            calendar_id: 日历的 `calendar_id`。
            event_id: 日程的 `event_id`。
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。

        Returns:
            参与人数据列表，每项包含 `attendee_id`、`type`、`rsvp_status` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取日程参与人列表](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event-attendee/list)

        Examples:
            >>> await client.calendar.attendees.list("feishu.cn_xxx", "evtxxx")  # doctest:+SKIP
            [{'attendee_id': 'axxx', 'type': 'user', ...}, ...]  # noqa: E501
        """
        return await self._client.paginate_get(
            f"calendar/v4/calendars/{quote_segment(calendar_id)}/events/{quote_segment(event_id)}/attendees",
            page_size=page_size,
            max_items=max_items,
        )
