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


class MeetingsNamespace(Namespace):
    r"""
    会议（Meeting）接口命名空间。

    通过 `client.vc.meetings` 访问，封装飞书视频会议中会议（meeting）相关的只读查询接口：获取单场会议详情、
    按会议号列举一段时间内的历史会议。会议在被发起后才会产生，与预约（reserve）是不同的对象。

    通常无需直接实例化，应通过 `client.vc.meetings` 访问。

    飞书文档:
        [获取会议详情](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/vc-v1/meeting/get)
    """

    async def get(
        self,
        meeting_id: str,
        *,
        with_participants: bool | None = None,
        with_meeting_ability: bool | None = None,
        user_id_type: str | None = None,
    ) -> NestedDict:
        r"""
        获取会议详情。

        Args:
            meeting_id: 会议 ID（9 位会议号对应的内部 ID，可由
                [`list_by_no`][feishu.vc.meetings.MeetingsNamespace.list_by_no] 获取）。
            with_participants: 是否返回参会人列表；为空时使用接口默认值（不返回）。
            with_meeting_ability: 是否返回会议能力用量统计；为空时使用接口默认值（不返回）。
            user_id_type: 用户 ID 的类型，如 `open_id`、`union_id`、`user_id`；为空时使用接口默认值。

        Returns:
            会议数据，含 `meeting` 字段，内含 `id`、`topic`、`url`、`meeting_no`、`start_time`、`end_time`、
            `host_user`、`status`（1 呼叫中、2 进行中、3 已结束）、`participant_count` 等信息；
            当 `with_participants` 为真时还含 `participants` 列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取会议详情](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/vc-v1/meeting/get)

        Examples:
            >>> await client.vc.meetings.get("700...", with_participants=True)  # doctest:+SKIP
            {'meeting': {'id': '700...', 'topic': '周会', 'status': 3, 'participant_count': '5'}}
        """
        params: dict[str, Any] = {}
        if with_participants is not None:
            params["with_participants"] = with_participants
        if with_meeting_ability is not None:
            params["with_meeting_ability"] = with_meeting_ability
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        return await self._request_data("GET", f"vc/v1/meetings/{quote_segment(meeting_id)}", params=params)

    async def list_by_no(
        self,
        meeting_no: str,
        start_time: str,
        end_time: str,
        *,
        page_size: int | None = None,
        max_items: int | None = None,
    ) -> list[NestedDict]:
        r"""
        按会议号列举历史会议。

        同一会议号在不同时间会对应多场会议，本接口返回 `[start_time, end_time]` 时间窗内、该会议号下的
        历次会议，并自动翻页汇总为列表。

        Args:
            meeting_no: 9 位会议号。
            start_time: 查询起始时间（Unix 秒，字符串）。
            end_time: 查询结束时间（Unix 秒，字符串）。
            page_size: 每页条数；为空时使用接口默认值，超出上限时由客户端截断。
            max_items: 最多返回的会议数；为空表示返回全部。

        Returns:
            会议简要信息列表，每项含 `id`、`meeting_no`、`topic`、`url`、`note_id` 等字段；
            该时间窗内无会议时返回空列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取与会议号关联的会议列表](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/vc-v1/meeting/list_by_no)

        Examples:
            >>> await client.vc.meetings.list_by_no("123456789", "1699999000", "1700002600")  # doctest:+SKIP
            [{'id': '700...', 'meeting_no': '123456789', 'topic': '周会'}]
        """
        return await self._client.paginate_get(
            "vc/v1/meetings/list_by_no",
            params={"meeting_no": meeting_no, "start_time": start_time, "end_time": end_time},
            page_size=page_size,
            max_items=max_items,
            items_key="meeting_briefs",
        )
