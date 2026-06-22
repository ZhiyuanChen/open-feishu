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
    from .meetings import MeetingsNamespace
    from .reserves import ReservesNamespace


class VCNamespace(Namespace):
    r"""
    视频会议（VC）接口命名空间。

    通过 `client.vc` 访问，作为预约与会议两个子命名空间的入口：
    [`VCNamespace.reserves`][feishu.vc.vc.VCNamespace.reserves] 暴露会议预约（reserve）能力，
    [`VCNamespace.meetings`][feishu.vc.vc.VCNamespace.meetings] 暴露会议（meeting）查询能力。
    两个子命名空间均在首次访问时惰性创建。

    通常无需直接实例化，应通过 `client.vc` 访问。

    飞书文档:
        [视频会议概述](https://open.feishu.cn/document/server-docs/vc-v1/vc-overview)
    """

    _meetings: MeetingsNamespace | None = None
    _reserves: ReservesNamespace | None = None

    @property
    def meetings(self) -> MeetingsNamespace:
        r"""
        会议接口命名空间。

        惰性创建并返回 [feishu.vc.meetings.MeetingsNamespace][]，用于查询会议详情与按会议号列举历史会议。

        Returns:
            会议接口命名空间实例。

        飞书文档:
            [获取会议详情](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/vc-v1/meeting/get)

        Examples:
            >>> client.vc.meetings  # doctest:+SKIP
            <feishu.vc.meetings.MeetingsNamespace object at ...>
        """
        if self._meetings is None:
            from .meetings import MeetingsNamespace

            self._meetings = MeetingsNamespace(self._client)
        return self._meetings

    @property
    def reserves(self) -> ReservesNamespace:
        r"""
        会议预约接口命名空间。

        惰性创建并返回 [feishu.vc.reserves.ReservesNamespace][]，用于预约会议以及对预约的查询、更新与删除。

        Returns:
            会议预约接口命名空间实例。

        飞书文档:
            [预约会议](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/vc-v1/reserve/apply)

        Examples:
            >>> client.vc.reserves  # doctest:+SKIP
            <feishu.vc.reserves.ReservesNamespace object at ...>
        """
        if self._reserves is None:
            from .reserves import ReservesNamespace

            self._reserves = ReservesNamespace(self._client)
        return self._reserves
