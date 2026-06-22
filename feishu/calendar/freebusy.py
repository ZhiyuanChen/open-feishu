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


class FreebusyNamespace(Namespace):
    r"""
    忙闲接口命名空间。

    通过 `client.calendar.freebusy` 访问，封装飞书日历中忙闲（freebusy）查询相关的服务端接口。

    通常无需直接实例化，应通过 `client.calendar.freebusy` 访问。

    飞书文档:
        [查询主日历忙闲信息](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event/list-2)
    """

    async def query(self, body: dict[str, Any]) -> NestedDict:
        r"""
        查询主日历忙闲信息。

        `body` 是描述查询条件的请求体，原样作为 JSON 发送，常见键包括 `time_min`、
        `time_max`（均为 RFC 3339 时间），以及 `user_id`、`room_id` 二者择一的被查询对象标识。

        Args:
            body: 忙闲查询请求体，原样作为 JSON 发送，例如
                `{"time_min": "...", "time_max": "...", "user_id": "ou_xxx"}`。

        Returns:
            忙闲查询结果数据，含 `freebusy_list`（每项含 `start_time`、`end_time`）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [查询主日历忙闲信息](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event/list-2)

        Examples:
            >>> await client.calendar.freebusy.query({"time_min": "...", "time_max": "...", "user_id": "ou_xxx"})  # doctest:+SKIP
            {'freebusy_list': [{'start_time': '...', 'end_time': '...'}, ...]}  # noqa: E501
        """
        return await self._request_data("POST", "calendar/v4/freebusy/list", json=body)
