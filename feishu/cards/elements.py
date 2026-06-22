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

from .markdown import escape_markdown
from .validation import clamp_spacing, validate_element_id


def _plain_text(content: str) -> dict[str, Any]:
    return {"tag": "plain_text", "content": content}


def md(
    content: str,
    *,
    text_align: str | None = None,
    text_size: str | None = None,
    escape: bool = False,
    element_id: str | None = None,
) -> dict[str, Any]:
    r"""
    构造卡片 2.0 的 markdown 元素（tag 为 `markdown`，而非旧版的 `div`）。

    Args:
        content: markdown 文本内容。
        text_align: 文本对齐方式，如 `left`、`center`、`right`。
        text_size: 文本字号，如 `normal`、`heading`。
        escape: 是否先对 `content` 调用 [feishu.cards.markdown.escape_markdown][] 转义控制字符。
        element_id: 元素的自定义 ID，会经 [feishu.cards.validation.validate_element_id][] 校验。

    Returns:
        markdown 元素的字典表示。

    飞书文档:
        [富文本组件](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/content-components/rich-text)

    Examples:
        >>> md("hello")
        {'tag': 'markdown', 'content': 'hello'}
        >>> md("a*b", escape=True)["content"]
        'a&#42;b'
        >>> md("h", text_align="center", element_id="md")
        {'tag': 'markdown', 'content': 'h', 'text_align': 'center', 'element_id': 'md'}
    """
    if escape:
        content = escape_markdown(content)
    el: dict[str, Any] = {"tag": "markdown", "content": content}
    if text_align is not None:
        el["text_align"] = text_align
    if text_size is not None:
        el["text_size"] = text_size
    if element_id is not None:
        el["element_id"] = validate_element_id(element_id)
    return el


def hr() -> dict[str, Any]:
    r"""
    构造卡片 2.0 的分割线元素。

    Returns:
        分割线元素的字典表示。

    飞书文档:
        [分割线组件](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/content-components/divider)

    Examples:
        >>> hr()
        {'tag': 'hr'}
    """
    return {"tag": "hr"}


def img(img_key: str, alt: str, *, element_id: str | None = None, **opts: Any) -> dict[str, Any]:
    r"""
    构造卡片 2.0 的图片元素。

    Args:
        img_key: 图片的 key，通过上传图片接口获取。
        alt: 图片的悬浮提示文案（无障碍替代文本）。
        element_id: 元素的自定义 ID，会经 [feishu.cards.validation.validate_element_id][] 校验。
        **opts: 其余原样透传给元素字典的字段，如 `scale_type`、`size` 等。

    Returns:
        图片元素的字典表示。

    飞书文档:
        [图片组件](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/content-components/image)

    Examples:
        >>> el = img("img_v2_abc", "a cat", scale_type="crop_center")
        >>> el["tag"], el["img_key"], el["scale_type"]
        ('img', 'img_v2_abc', 'crop_center')
        >>> el["alt"]
        {'tag': 'plain_text', 'content': 'a cat'}
    """
    el: dict[str, Any] = {"tag": "img", "img_key": img_key, "alt": _plain_text(alt)}
    if element_id is not None:
        el["element_id"] = validate_element_id(element_id)
    el.update(opts)
    return el


def button(
    text: str,
    *,
    value: dict[str, Any] | None = None,
    url: str | None = None,
    type: str = "default",  # noqa: A002 - matches Feishu's field name
    confirm: dict[str, Any] | None = None,
    icon: dict[str, Any] | None = None,
    element_id: str | None = None,
) -> dict[str, Any]:
    r"""
    构造卡片 2.0 的按钮元素。

    `value` 会生成 `callback` 交互行为，`url` 会生成 `open_url` 交互行为，二者可同时存在。
    回调触发的 `card.action.trigger` 事件可用 [feishu.cards.callback.parse_action][] 解析。

    Args:
        text: 按钮文案。
        value: 点击回调时回传的业务数据，生成 `callback` 行为。
        url: 点击时跳转的链接，生成 `open_url` 行为。
        type: 按钮样式类型，如 `default`、`primary`、`danger`。
        confirm: 点击前的二次确认弹窗配置。
        icon: 按钮图标配置。
        element_id: 元素的自定义 ID，会经 [feishu.cards.validation.validate_element_id][] 校验。

    Returns:
        按钮元素的字典表示。

    飞书文档:
        [按钮组件](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/interactive-components/button)

    Examples:
        >>> button("Click", value={"k": "v"})["behaviors"]
        [{'type': 'callback', 'value': {'k': 'v'}}]
        >>> button("Open", url="https://x.com", type="primary")["behaviors"]
        [{'type': 'open_url', 'default_url': 'https://x.com'}]
        >>> button("Both", value={"a": 1}, url="https://x.com")["behaviors"]
        [{'type': 'callback', 'value': {'a': 1}}, {'type': 'open_url', 'default_url': 'https://x.com'}]
    """
    el: dict[str, Any] = {"tag": "button", "text": _plain_text(text), "type": type}
    behaviors: list[dict[str, Any]] = []
    if value is not None:
        behaviors.append({"type": "callback", "value": value})
    if url is not None:
        behaviors.append({"type": "open_url", "default_url": url})
    if behaviors:
        el["behaviors"] = behaviors
    if confirm is not None:
        el["confirm"] = confirm
    if icon is not None:
        el["icon"] = icon
    if element_id is not None:
        el["element_id"] = validate_element_id(element_id)
    return el


def column_set(
    columns: list[dict[str, Any]],
    *,
    flex_mode: str = "none",
    horizontal_spacing: str | int | None = None,
    element_id: str | None = None,
    **opts: Any,
) -> dict[str, Any]:
    r"""
    构造卡片 2.0 的分栏（column_set）元素。

    Args:
        columns: 分栏列表，每一项为一个 `column` 字典。
        flex_mode: 列在窄屏下的自适应方式，如 `none`、`stretch`、`flow`、`bisect`、`trisect`。
        horizontal_spacing: 列间水平间距；为整数时会经 [feishu.cards.validation.clamp_spacing][] 裁剪到 `[-99, 99]`。
        element_id: 元素的自定义 ID，会经 [feishu.cards.validation.validate_element_id][] 校验。
        **opts: 其余原样透传给元素字典的字段。

    Returns:
        分栏元素的字典表示。

    飞书文档:
        [多列布局组件](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/containers/column-set)

    Examples:
        >>> column_set([{"tag": "column", "elements": []}], flex_mode="stretch")
        {'tag': 'column_set', 'flex_mode': 'stretch', 'columns': [{'tag': 'column', 'elements': []}]}
        >>> column_set([], horizontal_spacing=200)["horizontal_spacing"]
        99
    """
    el: dict[str, Any] = {"tag": "column_set", "flex_mode": flex_mode, "columns": columns}
    if horizontal_spacing is not None:
        if isinstance(horizontal_spacing, int) and not isinstance(horizontal_spacing, bool):
            horizontal_spacing = clamp_spacing(horizontal_spacing)
        el["horizontal_spacing"] = horizontal_spacing
    if element_id is not None:
        el["element_id"] = validate_element_id(element_id)
    el.update(opts)
    return el
