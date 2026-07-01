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

# Feishu (lark) markdown control-character -> HTML-entity escape table, per Feishu docs
# (see the rich-text component doc URL in escape_markdown below). Hand-maintained to
# mirror Feishu's rendering, not generated from an official source.
# Numeric entities except '~' which uses the named &sim; entity.
MARKDOWN_ENTITIES: dict[str, str] = {
    "*": "&#42;",
    "_": "&#95;",
    "`": "&#96;",
    "<": "&#60;",
    ">": "&#62;",
    "[": "&#91;",
    "]": "&#93;",
    "(": "&#40;",
    ")": "&#41;",
    "#": "&#35;",
    "\\": "&#92;",
    "~": "&sim;",
    "!": "&#33;",
    "+": "&#43;",
    "-": "&#45;",
    ".": "&#46;",
    "|": "&#124;",
}

# Build a str.translate table once at import. translate() scans each source
# character exactly once and never re-scans inserted text, so emitted entities
# are never re-escaped and replacement order is irrelevant.
_TRANSLATION = str.maketrans(MARKDOWN_ENTITIES)


def escape_markdown(text: str) -> str:
    r"""
    将飞书（lark）markdown 控制字符转义为对应的 HTML 实体（依据下方飞书文档，手工维护）。

    用于将任意用户或大模型生成的文本安全地嵌入卡片 2.0 的 `markdown` 元素中，
    使其中的控制字符不会破坏渲染效果。

    Args:
        text: 待转义的原始文本。

    Returns:
        所有控制字符均被替换为 HTML 实体后的文本。

    飞书文档:
        [Markdown标签](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/content-components/rich-text)

    Examples:
        >>> escape_markdown("a*b_c")
        'a&#42;b&#95;c'
        >>> escape_markdown("~")
        '&sim;'
        >>> escape_markdown("hello world 123")
        'hello world 123'
    """
    return text.translate(_TRANSLATION)
