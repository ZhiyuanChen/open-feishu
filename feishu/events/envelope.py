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


class Event:
    r"""
    飞书事件载荷的统一视图，同时兼容 1.0 与 2.0 两种事件格式。

    飞书早期（1.0）事件将类型、`uuid`、`ts`、`token` 放在顶层，事件正文在 `event` 中；
    新版（2.0）事件则将这些元信息收敛到 `header` 字段。`Event` 屏蔽了二者差异，
    使调用方无需关心具体 schema 即可读取 [event_type][feishu.events.envelope.Event.event_type]、
    [event_id][feishu.events.envelope.Event.event_id] 等字段。

    通常无需直接构造，应使用 [from_payload][feishu.events.envelope.Event.from_payload] 由原始载荷推断。

    飞书文档:
        [事件格式](https://open.feishu.cn/document/server-docs/event-subscription-guide/overview)

    Examples:
        >>> ev = Event.from_payload(
        ...     {"schema": "2.0", "header": {"event_type": "im.message.receive_v1", "event_id": "evt_2"}, "event": {}}
        ... )
        >>> ev.schema_version
        '2.0'
        >>> ev.event_type
        'im.message.receive_v1'
    """

    __slots__ = ("_raw", "_schema_version")

    def __init__(self, raw: NestedDict, schema_version: str):
        self._raw = raw
        self._schema_version = schema_version

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Event:
        r"""
        从原始事件载荷构造 [Event][feishu.events.envelope.Event] 并自动推断 schema 版本。

        当载荷含有 `"schema": "2.0"` 或顶层存在 `header` 字段时判定为 2.0，否则视为 1.0。
        传入的 `dict` 会被包装为 `NestedDict` 以支持点号取值。

        Args:
            payload: 解密、解析后的事件载荷字典。

        Returns:
            统一封装后的事件对象。

        Examples:
            >>> Event.from_payload({"uuid": "u_1", "event": {"type": "message"}}).schema_version
            '1.0'
            >>> Event.from_payload({"header": {"event_type": "card.action.trigger"}, "event": {}}).schema_version
            '2.0'
        """
        raw = payload if isinstance(payload, NestedDict) else NestedDict(payload)
        is_2_0 = raw.get("schema") == "2.0" or "header" in raw
        return cls(raw, "2.0" if is_2_0 else "1.0")

    @property
    def schema_version(self) -> str:
        r"""
        事件的 schema 版本，`"1.0"` 或 `"2.0"`。
        """
        return self._schema_version

    @property
    def raw(self) -> NestedDict:
        r"""
        未经处理的原始事件载荷，便于访问本类未封装的字段。
        """
        return self._raw

    @property
    def _header(self) -> NestedDict:
        header = self._raw.get("header")
        return header if isinstance(header, NestedDict) else NestedDict(header or {})

    @property
    def _event(self) -> NestedDict:
        event = self._raw.get("event")
        return event if isinstance(event, NestedDict) else NestedDict(event or {})

    @property
    def event_type(self) -> str:
        r"""
        事件类型，例如 `im.message.receive_v1`。

        2.0 取自 `header.event_type`，1.0 取自 `event.type`；缺失时返回空字符串。
        """
        if self._schema_version == "2.0":
            return self._header.get("event_type", "")
        return self._event.get("type", "")

    @property
    def event_id(self) -> str:
        r"""
        事件唯一标识，用于去重。

        2.0 取自 `header.event_id`，1.0 取自顶层 `uuid`。始终返回 `str`，缺失时返回空字符串
        而非 `None`，因此调用方无需进行 `None` 判空。
        """
        if self._schema_version == "2.0":
            return self._header.get("event_id", "")
        return self._raw.get("uuid", "")

    @property
    def create_time(self) -> str | None:
        r"""
        事件产生时间戳（毫秒，字符串形式）。

        2.0 取自 `header.create_time`，1.0 取自顶层 `ts`；缺失时返回 `None`。
        """
        if self._schema_version == "2.0":
            return self._header.get("create_time")
        return self._raw.get("ts")

    @property
    def tenant_key(self) -> str | None:
        r"""
        租户标识，仅 2.0 事件可用；1.0 事件恒为 `None`。
        """
        return self._header.get("tenant_key") if self._schema_version == "2.0" else None

    @property
    def app_id(self) -> str | None:
        r"""
        触发事件的应用 ID，仅 2.0 事件可用；1.0 事件恒为 `None`。
        """
        return self._header.get("app_id") if self._schema_version == "2.0" else None

    @property
    def token(self) -> str | None:
        r"""
        事件头中的校验 Token（Verification Token）。

        用于事件来源校验，并非更新卡片所需的凭证。2.0 取自 `header.token`，1.0 取自顶层 `token`。
        """
        if self._schema_version == "2.0":
            return self._header.get("token")
        return self._raw.get("token")

    @property
    def body(self) -> NestedDict:
        r"""
        事件正文，即载荷中的 `event` 字段。

        返回值始终是可安全索引的 `NestedDict`：即使原始载荷缺少 `event` 字段，也会返回空字典，
        调用方可直接 `.get()` 而不会触发 `KeyError`。

        Examples:
            >>> ev = Event.from_payload({"schema": "2.0", "header": {"event_type": "x"}})
            >>> ev.body.get("anything") is None
            True
        """
        return self._event
