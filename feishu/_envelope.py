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

from chanfig import NestedDict


def _data(envelope: NestedDict) -> NestedDict:
    r"""
    从飞书统一响应信封中取出 `data` 字段，并保留原始信封。

    飞书服务端接口的响应统一形如 `{"code": ..., "msg": ..., "data": {...}}`。本助手将
    `data` 规整为 [chanfig.NestedDict][]，同时通过 `raw_envelope` 属性挂回完整信封，
    便于调用方在需要时读取 `code`/`msg`/分页游标等顶层元信息。使用
    [object.__setattr__][] 写入是为了绕过 [chanfig.NestedDict][] 的键值语义，
    把 `raw_envelope` 作为普通实例属性附加而不污染数据本身。

    Args:
        envelope: 接口返回的完整响应信封。

    Returns:
        `data` 字段对应的 [chanfig.NestedDict][]，其 `raw_envelope` 属性指向原始信封。
    """
    data = envelope.get("data")
    data = NestedDict(data) if not isinstance(data, NestedDict) else data
    object.__setattr__(data, "raw_envelope", envelope)
    return data
