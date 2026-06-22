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

import re

# Confirmed Card 2.0 header color templates.
HEADER_TEMPLATES: frozenset[str] = frozenset(
    {
        "blue",
        "wathet",
        "turquoise",
        "green",
        "yellow",
        "orange",
        "red",
        "carmine",
        "violet",
        "purple",
        "indigo",
        "grey",
        "default",
    }
)

_ELEMENT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,19}$")

_SPACING_MIN = -99
_SPACING_MAX = 99


def validate_element_id(element_id: str) -> str:
    r"""
    校验卡片元素的 `element_id`，合法则原样返回。

    规则：长度不超过 20 个字符，以 ASCII 字母开头，其余字符仅可为 `[A-Za-z0-9_]`。

    Args:
        element_id: 待校验的元素 ID。

    Returns:
        校验通过的元素 ID（即传入值本身）。

    Raises:
        ValueError: 当 `element_id` 不符合命名规则时抛出。

    飞书文档:
        [卡片 JSON 2.0 结构](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-structure)

    Examples:
        >>> validate_element_id("Title_1")
        'Title_1'
        >>> validate_element_id("1bad")
        Traceback (most recent call last):
        ...
        ValueError: invalid element_id '1bad': must be <=20 chars, start with a letter, and contain only [A-Za-z0-9_]
    """
    if not _ELEMENT_ID_RE.match(element_id):
        raise ValueError(
            f"invalid element_id {element_id!r}: must be <=20 chars, start with a "
            f"letter, and contain only [A-Za-z0-9_]"
        )
    return element_id


def validate_template(template: str) -> str:
    r"""
    校验卡片标题栏的颜色主题，合法则原样返回。

    取值须为已确认的枚举之一（见 [feishu.cards.validation.HEADER_TEMPLATES][]）。

    Args:
        template: 待校验的标题栏颜色主题。

    Returns:
        校验通过的颜色主题（即传入值本身）。

    Raises:
        ValueError: 当 `template` 不在已确认的枚举范围内时抛出。

    飞书文档:
        [标题组件](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/containers/title)

    Examples:
        >>> validate_template("blue")
        'blue'
        >>> try:
        ...     validate_template("rainbow")
        ... except ValueError as exc:
        ...     print(str(exc).startswith("invalid header template 'rainbow'"))
        True
    """
    if template not in HEADER_TEMPLATES:
        raise ValueError(f"invalid header template {template!r}: expected one of {sorted(HEADER_TEMPLATES)}")
    return template


def clamp_spacing(value: int) -> int:
    r"""
    将间距/边距整数裁剪到文档约定的 `[-99, 99]` 区间内。

    Args:
        value: 待裁剪的间距整数。

    Returns:
        裁剪到 `[-99, 99]` 区间后的整数。

    Raises:
        TypeError: 当 `value` 不是 `int`（或为 `bool`）时抛出。

    飞书文档:
        [布局组件](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/containers/column-set)

    Examples:
        >>> clamp_spacing(50)
        50
        >>> clamp_spacing(200)
        99
        >>> clamp_spacing(-100)
        -99
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"spacing must be an int, got {type(value).__name__}")
    return max(_SPACING_MIN, min(_SPACING_MAX, value))
