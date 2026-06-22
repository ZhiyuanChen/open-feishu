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

from urllib.parse import quote


def quote_segment(value: str, *, safe: str = "") -> str:
    r"""
    对调用方提供的单个 URL 路径段进行百分号编码。

    将由调用方传入的路径段（如消息 ID、文档 token、文件 key 等）转义后再拼入请求路径，
    避免值中意外包含 `/`、`?`、`#` 等字符导致越权访问或路径注入。默认 `safe=""`，
    即对所有保留字符编码（普通的飞书 ID 不含特殊字符，转义后保持原样）。

    个别结构化路径段本身就以保留字符作为语法分隔符，例如电子表格的 range
    `<sheetId>!<起始>:<结束>`。这类场景可通过 `safe` 保留这些分隔符，使合法取值在
    线路上保持原样，同时仍对 `/` 等危险字符编码以防注入。

    Args:
        value: 待编码的单个路径段；非字符串会先转为字符串。
        safe: 额外保留、不进行编码的字符集合，默认 `""`（编码全部保留字符）。

    Returns:
        百分号编码后的安全路径段。

    Examples:
        >>> quote_segment("om_abc123")
        'om_abc123'
        >>> quote_segment("a/b?c#d")
        'a%2Fb%3Fc%23d'
        >>> quote_segment("Q7PlXT!A1:B2", safe="!:")
        'Q7PlXT!A1:B2'
        >>> quote_segment("evil/..!A1", safe="!:")
        'evil%2F..!A1'
    """
    return quote(str(value), safe=safe)
