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


class ReservesNamespace(Namespace):
    r"""
    会议预约（Reserve）接口命名空间。

    通过 `client.vc.reserves` 访问，封装飞书视频会议中预约（reserve）相关的服务端接口：预约会议、
    查询、更新与删除预约。预约成功后返回会议号与多种入会链接。

    通常无需直接实例化，应通过 `client.vc.reserves` 访问。

    飞书文档:
        [预约会议](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/vc-v1/reserve/apply)
    """

    async def apply(
        self,
        meeting_settings: dict[str, Any],
        *,
        end_time: str | None = None,
        owner_id: str | None = None,
        user_id_type: str | None = None,
    ) -> NestedDict:
        r"""
        预约会议。

        仅将显式传入的字段写入请求体，未设置的字段会被省略；未指定 `owner_id` 时，预约人默认为当前调用者。

        Args:
            meeting_settings: 会议设置，原样作为 JSON 发送，常见键包括 `topic`（会议主题）、
                `auto_record`、`call_setting`、`assign_host_list`、`password` 等。
            end_time: 预约到期时间（Unix 秒，字符串）；到期后预约自动失效，为空时使用接口默认时长。
            owner_id: 预约人 ID，其类型由 `user_id_type` 指定；为空时默认为当前调用者。
            user_id_type: 用户 ID 的类型，如 `open_id`、`union_id`、`user_id`；为空时使用接口默认值。

        Returns:
            预约结果数据，含 `reserve` 字段，内含 `id`（预约 ID）、`meeting_no`（会议号）、`url`（入会链接）、
            `app_link`（客户端入会链接）、`live_link`（直播链接）、`end_time` 等信息。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [预约会议](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/vc-v1/reserve/apply)

        Examples:
            >>> await client.vc.reserves.apply({"topic": "周会"}, end_time="1700000000")  # doctest:+SKIP
            {'reserve': {'id': '765...', 'meeting_no': '121...', 'url': 'https://vc.feishu.cn/j/121...'}}
        """
        body: dict[str, Any] = {"meeting_settings": meeting_settings}
        if end_time is not None:
            body["end_time"] = end_time
        if owner_id is not None:
            body["owner_id"] = owner_id
        params: dict[str, Any] = {}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        return await self._request_data("POST", "vc/v1/reserves/apply", params=params, json=body)

    async def delete(self, reserve_id: str) -> NestedDict:
        r"""
        删除会议预约。

        Args:
            reserve_id: 预约 ID（[`apply`][feishu.vc.reserves.ReservesNamespace.apply] 返回的 `reserve.id`）。

        Returns:
            空数据体（接口成功时不返回额外字段）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [删除预约](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/vc-v1/reserve/delete)

        Examples:
            >>> await client.vc.reserves.delete("765...")  # doctest:+SKIP
            {}
        """
        return await self._request_data("DELETE", f"vc/v1/reserves/{quote_segment(reserve_id)}")

    async def get(self, reserve_id: str, *, user_id_type: str | None = None) -> NestedDict:
        r"""
        查询会议预约。

        Args:
            reserve_id: 预约 ID（[`apply`][feishu.vc.reserves.ReservesNamespace.apply] 返回的 `reserve.id`）。
            user_id_type: 用户 ID 的类型，如 `open_id`、`union_id`、`user_id`；为空时使用接口默认值。

        Returns:
            预约数据，含 `reserve` 字段，内含 `id`、`meeting_no`、`url`、`live_link`、`end_time`、
            `expire_status`（到期状态：1 未到期、2 已到期）等信息。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取预约](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/vc-v1/reserve/get)

        Examples:
            >>> await client.vc.reserves.get("765...")  # doctest:+SKIP
            {'reserve': {'id': '765...', 'meeting_no': '121...', 'expire_status': 1}}
        """
        params: dict[str, Any] = {}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        return await self._request_data("GET", f"vc/v1/reserves/{quote_segment(reserve_id)}", params=params)

    async def update(
        self,
        reserve_id: str,
        *,
        end_time: str | None = None,
        meeting_settings: dict[str, Any] | None = None,
        user_id_type: str | None = None,
    ) -> NestedDict:
        r"""
        更新会议预约。

        仅将显式传入的字段写入请求体，未设置的字段会被省略。

        Args:
            reserve_id: 预约 ID（[`apply`][feishu.vc.reserves.ReservesNamespace.apply] 返回的 `reserve.id`）。
            end_time: 新的预约到期时间（Unix 秒，字符串）。
            meeting_settings: 新的会议设置，原样作为 JSON 发送，键同 `apply`。
            user_id_type: 用户 ID 的类型，如 `open_id`、`union_id`、`user_id`；为空时使用接口默认值。

        Returns:
            更新后的预约数据，含 `reserve` 字段（结构同 [`get`][feishu.vc.reserves.ReservesNamespace.get]）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [更新预约](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/vc-v1/reserve/update)

        Examples:
            >>> await client.vc.reserves.update("765...", end_time="1700003600")  # doctest:+SKIP
            {'reserve': {'id': '765...', 'end_time': '1700003600'}}
        """
        body: dict[str, Any] = {}
        if end_time is not None:
            body["end_time"] = end_time
        if meeting_settings is not None:
            body["meeting_settings"] = meeting_settings
        params: dict[str, Any] = {}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        return await self._request_data("PUT", f"vc/v1/reserves/{quote_segment(reserve_id)}", params=params, json=body)
