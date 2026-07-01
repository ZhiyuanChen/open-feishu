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


def bitable_record(
    fields: dict[str, Any],
    *,
    record_id: str | None = None,
    **extra: Any,
) -> NestedDict:
    r"""
    构造飞书多维表格记录载荷。

    Args:
        fields: 记录字段值，键为多维表格字段名。
        record_id: 可选记录 ID，通常用于更新记录载荷。
        extra: 其他需要透传的记录字段；值为 `None` 时忽略。

    Returns:
        形如 `{"fields": {...}}` 的记录载荷，可传给
        [feishu.bitable.records.RecordsNamespace.create][] /
        [feishu.bitable.records.RecordsNamespace.update][]。

    Examples:
        >>> bitable_record({"Title": "hi"}).fields.Title
        'hi'
    """
    payload = NestedDict(fields=NestedDict(fields))
    if record_id:
        payload.record_id = record_id
    for key, value in extra.items():
        if value is not None:
            payload[key] = value
    return payload
