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


class CalendarsNamespace(Namespace):
    r"""
    日历接口命名空间。

    通过 `client.calendar.calendars` 访问，封装飞书日历（calendar）相关的服务端接口，包括日历的创建、
    查询、更新、删除与列举，以及主日历查询等能力。日历以 `calendar_id` 标识。

    通常无需直接实例化，应通过 `client.calendar.calendars` 访问。

    飞书文档:
        [日历概述](https://open.feishu.cn/document/server-docs/calendar-v4/calendar/introduction)
    """

    async def create(self, calendar: dict[str, Any]) -> NestedDict:
        r"""
        创建共享日历。

        `calendar` 是描述待创建日历的请求体，原样作为 JSON 发送，常见键包括 `summary`、
        `description`、`permissions`、`color`、`summary_alias` 等。

        Args:
            calendar: 日历定义对象，例如 `{"summary": "团队日历", "color": -1}`。

        Returns:
            包含 `calendar` 字段的数据，`calendar` 内含新建日历的 `calendar_id`、`summary`、
            `type`、`role` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [创建共享日历](https://open.feishu.cn/document/server-docs/calendar-v4/calendar/create)

        Examples:
            >>> await client.calendar.calendars.create({"summary": "团队日历"})  # doctest:+SKIP
            {'calendar': {'calendar_id': 'feishu.cn_xxx', 'summary': '团队日历', ...}}  # noqa: E501
        """
        return await self._request_data("POST", "calendar/v4/calendars", json=calendar)

    async def delete(self, calendar_id: str) -> NestedDict:
        r"""
        删除共享日历。

        Args:
            calendar_id: 待删除日历的 `calendar_id`。

        Returns:
            接口返回的数据（通常为空）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [删除共享日历](https://open.feishu.cn/document/server-docs/calendar-v4/calendar/delete)

        Examples:
            >>> await client.calendar.calendars.delete("feishu.cn_xxx")  # doctest:+SKIP
            {}
        """
        return await self._request_data("DELETE", f"calendar/v4/calendars/{quote_segment(calendar_id)}")

    async def get(self, calendar_id: str) -> NestedDict:
        r"""
        获取日历信息。

        Args:
            calendar_id: 日历的唯一标识 `calendar_id`。

        Returns:
            包含 `calendar` 字段的数据，`calendar` 内含 `calendar_id`、`summary`、
            `description`、`type`、`role` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [查询日历信息](https://open.feishu.cn/document/server-docs/calendar-v4/calendar/get)

        Examples:
            >>> await client.calendar.calendars.get("feishu.cn_xxx")  # doctest:+SKIP
            {'calendar': {'calendar_id': 'feishu.cn_xxx', 'summary': '团队日历', ...}}  # noqa: E501
        """
        return await self._request_data("GET", f"calendar/v4/calendars/{quote_segment(calendar_id)}")

    async def list(self, *, page_size: int = 50, max_items: int | None = None) -> list[NestedDict]:
        r"""
        查询日历列表。

        自动翻页并将各页结果拼接为单个列表返回。`page_size` 会被限制在
        [feishu.consts.MAX_PAGE_SIZE][] 以内。日历列表的条目位于响应体的 `calendar_list`
        字段下。

        Args:
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。

        Returns:
            日历数据列表，每项包含 `calendar_id`、`summary`、`type`、`role` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [查询日历列表](https://open.feishu.cn/document/server-docs/calendar-v4/calendar/list)

        Examples:
            >>> await client.calendar.calendars.list()  # doctest:+SKIP
            [{'calendar_id': 'feishu.cn_xxx', 'summary': '团队日历', ...}, ...]  # noqa: E501
        """
        return await self._client.paginate_get(
            "calendar/v4/calendars", page_size=page_size, max_items=max_items, items_key="calendar_list"
        )

    async def primary(self, *, user_id_type: str | None = None) -> NestedDict:
        r"""
        查询主日历信息。

        获取当前身份的主日历（primary calendar）。仅当显式传入 `user_id_type` 时才将其
        并入查询参数，未设置则省略。

        Args:
            user_id_type: 返回的用户 ID 类型，如 `open_id`、`union_id`、`user_id`；
                为空时省略该参数。

        Returns:
            包含 `calendars` 字段的数据，`calendars` 为元素形如 `{"calendar": {...},
            "user_id": "..."}` 的列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [查询主日历信息](https://open.feishu.cn/document/server-docs/calendar-v4/calendar/primary)

        Examples:
            >>> await client.calendar.calendars.primary()  # doctest:+SKIP
            {'calendars': [{'calendar': {'calendar_id': 'feishu.cn_xxx', ...}, 'user_id': 'ou_xxx'}]}  # noqa: E501
        """
        params = {}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        return await self._request_data("POST", "calendar/v4/calendars/primary", params=params)

    async def update(self, calendar_id: str, calendar: dict[str, Any]) -> NestedDict:
        r"""
        更新日历信息。

        `calendar` 是描述待更新字段的请求体，原样作为 JSON 发送；仅传入的字段会被更新，
        未传入的字段保持不变，常见键包括 `summary`、`description`、`permissions`、`color`
        等。

        Args:
            calendar_id: 待更新日历的 `calendar_id`。
            calendar: 待更新字段的映射，例如 `{"summary": "新名称"}`。

        Returns:
            包含 `calendar` 字段的数据，`calendar` 内含更新后日历的 `calendar_id`、`summary`
            等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [更新日历信息](https://open.feishu.cn/document/server-docs/calendar-v4/calendar/patch)

        Examples:
            >>> await client.calendar.calendars.update("feishu.cn_xxx", {"summary": "新名称"})  # doctest:+SKIP
            {'calendar': {'calendar_id': 'feishu.cn_xxx', 'summary': '新名称', ...}}  # noqa: E501
        """
        return await self._request_data("PATCH", f"calendar/v4/calendars/{quote_segment(calendar_id)}", json=calendar)
