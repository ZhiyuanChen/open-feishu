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

from .builder import Card


def text_card(content: str, title: str | None = None, template: str = "blue") -> dict[str, Any]:
    r"""
    构造仅含一个 markdown 元素的卡片，并在提供 `title` 时附带标题栏。

    Args:
        content: markdown 正文内容。
        title: 标题栏文案；为 `None` 时不生成标题栏。
        template: 标题栏颜色主题，会经 [feishu.cards.validation.validate_template][] 校验。

    Returns:
        卡片 2.0 的字典表示。

    飞书文档:
        [卡片 JSON 2.0 结构](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-structure)

    Examples:
        >>> d = text_card("hello", title="Greeting", template="green")
        >>> d["header"]["title"], d["header"]["template"]
        ({'tag': 'plain_text', 'content': 'Greeting'}, 'green')
        >>> d["body"]["elements"]
        [{'tag': 'markdown', 'content': 'hello'}]
        >>> "header" in text_card("hello")
        False
    """
    card = Card()
    if title is not None:
        card.header(title, template=template)
    card.markdown(content)
    return card.to_dict()


def alert_card(
    content: str,
    title: str,
    template: str = "red",
    buttons: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    r"""
    构造含标题栏、markdown 正文，以及可选按钮的告警卡片。

    Args:
        content: markdown 正文内容。
        title: 标题栏文案。
        template: 标题栏颜色主题，默认 `red`，会经 [feishu.cards.validation.validate_template][] 校验。
        buttons: 预先构造好的按钮元素列表（如由 [feishu.cards.elements.button][] 生成），依次追加到正文之后。

    Returns:
        卡片 2.0 的字典表示。

    飞书文档:
        [卡片 JSON 2.0 结构](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-structure)

    Examples:
        >>> from feishu.cards.elements import button
        >>> d = alert_card("Heads up", title="Alert", buttons=[button("OK", value={"d": "y"})])
        >>> d["header"]["template"]
        'red'
        >>> [e["tag"] for e in d["body"]["elements"]]
        ['markdown', 'button']
    """
    card = Card().header(title, template=template).markdown(content)
    for btn in buttons or []:
        card.add(btn)
    return card.to_dict()


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _gfm_table(headers: list[str], rows: list[list[Any]]) -> str:
    for i, row in enumerate(rows):
        if len(row) != len(headers):
            raise ValueError(f"table_card row {i} has {len(row)} cells, expected {len(headers)}")

    def line(cells: list[Any]) -> str:
        return "| " + " | ".join(_cell(c) for c in cells) + " |"

    separator = "| " + " | ".join("---" for _ in headers) + " |"
    lines = [line(headers), separator]
    lines.extend(line(row) for row in rows)
    return "\n".join(lines)


def table_card(
    headers: list[str],
    rows: list[list[Any]],
    title: str | None = None,
) -> dict[str, Any]:
    r"""
    将表头与数据行渲染为 GFM markdown 表格，置于单个 markdown 元素中。

    采用稳妥的 markdown 表格实现，而非卡片 2.0 原生 `table` 组件。单元格中的竖线 `|`
    会被转义，换行符会折叠为空格，以免破坏表格结构。

    Args:
        headers: 表头文案列表。
        rows: 数据行列表，每一行的单元格数必须与 `headers` 等长。
        title: 标题栏文案；为 `None` 时不生成标题栏。

    Returns:
        卡片 2.0 的字典表示。

    Raises:
        ValueError: 当某一行的单元格数与 `headers` 长度不一致时抛出。

    飞书文档:
        [富文本组件](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/content-components/rich-text)

    Examples:
        >>> d = table_card(["Name", "Age"], [["Alice", 30], ["Bob", 25]])
        >>> print(d["body"]["elements"][0]["content"])
        | Name | Age |
        | --- | --- |
        | Alice | 30 |
        | Bob | 25 |
        >>> table_card(["A", "B"], [["only-one"]])
        Traceback (most recent call last):
        ...
        ValueError: table_card row 0 has 1 cells, expected 2
    """
    card = Card()
    if title is not None:
        card.header(title)
    card.markdown(_gfm_table(headers, rows))
    return card.to_dict()
