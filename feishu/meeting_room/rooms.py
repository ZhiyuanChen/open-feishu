# OpenFeishu
# Copyright (C) 2024-Present  DanLing

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeAlias

from chanfig import NestedDict

from .._namespace import Namespace
from ..calendar import DEFAULT_TIMEZONE, rfc3339

_NestedDictList: TypeAlias = list[NestedDict]


class MeetingRoomNamespace(Namespace):
    r"""
    会议室接口命名空间。

    通过 `client.meeting_room` 访问，封装会议室列表、详情与忙闲查询能力。
    预订会议室本身通过创建日程后添加 `type=resource` 的日程参与人完成。

    飞书文档:
        [获取会议室列表](https://open.feishu.cn/document/ukTMukTMukTM/uADOyUjLwgjM14CM4ITN)

        [查询会议室详情](https://open.feishu.cn/document/ukTMukTMukTM/uEDOyUjLxgjM14SM4ITN)

        [查询会议室忙闲](https://open.feishu.cn/document/server-docs/calendar-v4/meeting-room-event/query-room-availability)
    """

    async def list_buildings(
        self,
        *,
        order_by: str | None = None,
        fields: str | Sequence[str] | None = None,
        page_size: int = 100,
        max_items: int | None = None,
    ) -> _NestedDictList:
        r"""
        获取建筑物列表。

        Args:
            order_by: 排序，如 `name-asc`、`name-desc`。
            fields: 返回字段，字符串或字符串序列；为空时使用接口默认字段。
            page_size: 每页数量。
            max_items: 最多返回数量。

        Returns:
            飞书原始建筑物对象列表，每项为一个建筑物数据字典。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。
        """
        return await self._client.paginate_get(
            "meeting_room/building/list",
            params={"order_by": order_by, "fields": _fields(fields)},
            page_size=page_size,
            max_items=max_items,
            items_key="buildings",
        )

    async def batch_get_buildings(
        self,
        building_ids: Sequence[str],
        *,
        fields: str | Sequence[str] | None = None,
    ) -> _NestedDictList:
        r"""
        查询建筑物详情。

        Args:
            building_ids: 建筑物 ID 列表。
            fields: 返回字段，字符串或字符串序列；为空时使用接口默认字段。

        Returns:
            飞书原始建筑物详情对象列表，每项为一个建筑物数据字典。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。
        """
        data = await self._request_data(
            "GET",
            "meeting_room/building/batch_get",
            params={"building_ids": list(building_ids), "fields": _fields(fields)},
        )
        return list(data.get("buildings") or [])

    async def list(
        self,
        *,
        building_id: str | None = None,
        order_by: str | None = None,
        fields: str | Sequence[str] | None = None,
        page_size: int = 100,
        max_items: int | None = None,
    ) -> _NestedDictList:
        r"""
        获取会议室列表（按建筑筛选）。

        本方法调用旧版 `meeting_room/room/list`，其文档化的筛选参数是 `building_id`。会议室「层级」
        （room_level_id）筛选属于新版 `vc/v1/rooms` 接口，本方法不支持，故不暴露该参数以免静默失效。

        Args:
            building_id: 建筑物 ID（按建筑筛选会议室）。
            order_by: 排序，如 `name-asc`、`name-desc`、`floor_name-asc`、`floor_name-desc`。
            fields: 返回字段，字符串或字符串序列；为空时使用接口默认字段。
            page_size: 每页数量。
            max_items: 最多返回数量。
        """
        params = {
            "building_id": building_id,
            "order_by": order_by,
            "fields": _fields(fields),
        }
        return await self._client.paginate_get(
            "meeting_room/room/list",
            params=params,
            page_size=page_size,
            max_items=max_items,
            items_key="rooms",
        )

    async def batch_get(
        self,
        room_ids: Sequence[str],
        *,
        fields: str | Sequence[str] | None = None,
    ) -> _NestedDictList:
        r"""
        查询会议室详情。

        Args:
            room_ids: 会议室 ID 列表。
            fields: 返回字段，字符串或字符串序列；为空时使用接口默认字段。

        Returns:
            飞书原始会议室详情对象列表，每项为一个会议室数据字典。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。
        """
        params = {"room_ids": list(room_ids), "fields": _fields(fields)}
        data = await self._request_data("GET", "meeting_room/room/batch_get", params=params)
        return list(data.get("rooms") or [])

    async def freebusy(
        self,
        room_ids: Sequence[str],
        *,
        time_min: Any,
        time_max: Any,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> NestedDict:
        r"""
        查询会议室忙闲。

        Args:
            room_ids: 会议室 ID 列表。
            time_min: 查询开始时间，支持 ISO/RFC3339 字符串、Unix 秒等。
            time_max: 查询结束时间，支持 ISO/RFC3339 字符串、Unix 秒等。
            timezone: 当时间没有时区时使用的默认时区。

        Returns:
            忙闲查询结果数据，含 `freebusy`（每项含 `start_time`、`end_time`、`room_id`）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。
        """
        params = {
            "room_ids": list(room_ids),
            "time_min": rfc3339(time_min, timezone=timezone),
            "time_max": rfc3339(time_max, timezone=timezone),
        }
        return await self._request_data("GET", "meeting_room/freebusy/batch_get", params=params)


def _fields(fields: str | Sequence[str] | None) -> str | None:
    if fields is None:
        return None
    if isinstance(fields, str):
        return fields
    return ",".join(fields)
