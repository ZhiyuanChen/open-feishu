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

from typing import Any, cast

from . import elements
from .validation import validate_template


class ColumnSet:
    r"""
    卡片 2.0 分栏（column_set）元素的子构造器。

    通过链式调用 `.column(...)` 逐列添加内容，再用 [feishu.cards.builder.ColumnSet.to_dict][]
    生成元素字典；若绑定了父级 [feishu.cards.builder.Card][]，可用
    [feishu.cards.builder.ColumnSet.end][] 将其追加回父卡片。

    飞书文档:
        [多列布局组件](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/containers/column-set)

    Examples:
        >>> cs = ColumnSet(flex_mode="stretch").column({"tag": "markdown", "content": "a"}, weight=1)
        >>> d = cs.to_dict()
        >>> d["tag"], d["flex_mode"]
        ('column_set', 'stretch')
        >>> d["columns"][0]
        {'tag': 'column', 'width': 'auto', 'elements': [{'tag': 'markdown', 'content': 'a'}], 'weight': 1}
    """

    def __init__(
        self,
        *,
        flex_mode: str = "none",
        horizontal_spacing: str | int | None = None,
        parent: Card | None = None,
        **opts: Any,
    ):
        self._flex_mode = flex_mode
        self._horizontal_spacing = horizontal_spacing
        self._parent = parent
        self._opts = opts
        self._columns: list[dict[str, Any]] = []

    def column(
        self,
        *els: dict[str, Any],
        width: str = "auto",
        weight: int | None = None,
        vertical_align: str | None = None,
        **opts: Any,
    ) -> ColumnSet:
        r"""
        追加一列，列内含给定的元素及配置，并返回自身以便链式调用。

        Args:
            *els: 该列内的元素字典。
            width: 列宽，如 `auto`、`weighted`。
            weight: 当 `width="weighted"` 时该列所占权重。
            vertical_align: 列内元素的垂直对齐方式，如 `top`、`center`、`bottom`。
            **opts: 其余原样透传给该列字典的字段。

        Returns:
            当前 [feishu.cards.builder.ColumnSet][] 实例。

        Examples:
            >>> cs = ColumnSet().column({"tag": "markdown", "content": "x"}, width="weighted", weight=2)
            >>> cs.to_dict()["columns"][0]["weight"]
            2
        """
        col: dict[str, Any] = {"tag": "column", "width": width, "elements": list(els)}
        if weight is not None:
            col["weight"] = weight
        if vertical_align is not None:
            col["vertical_align"] = vertical_align
        col.update(opts)
        self._columns.append(col)
        return self

    def to_dict(self) -> dict[str, Any]:
        r"""
        生成分栏元素字典，委托给 [feishu.cards.elements.column_set][] 实现。

        Returns:
            分栏（column_set）元素的字典表示。

        Examples:
            >>> ColumnSet(flex_mode="stretch").to_dict()
            {'tag': 'column_set', 'flex_mode': 'stretch', 'columns': []}
        """
        return elements.column_set(
            self._columns,
            flex_mode=self._flex_mode,
            horizontal_spacing=self._horizontal_spacing,
            **self._opts,
        )

    def end(self) -> Card:
        r"""
        将本分栏追加到绑定的父级 [feishu.cards.builder.Card][]，并返回该父卡片。

        Returns:
            绑定的父级 [feishu.cards.builder.Card][] 实例。

        Raises:
            ValueError: 当本 `ColumnSet` 未绑定父卡片（独立构造）时抛出。

        Examples:
            >>> card = Card()
            >>> card.column_set().column({"tag": "markdown", "content": "a"}).end() is card
            True
        """
        if self._parent is None:
            raise ValueError("ColumnSet.end() requires a parent Card; use Card.column_set()")
        self._parent.add(self.to_dict())
        return self._parent


class Card:
    r"""
    飞书卡片 JSON 2.0 的链式构造器。

    链式调用各方法逐步拼装卡片，再用 [feishu.cards.builder.Card.to_dict][] 或其别名
    `build` 生成最终的 `{"schema": "2.0", ...}` 字典。未设置过 `config` 与 `header` 时，
    它们不会出现在输出中。

    飞书文档:
        [卡片 JSON 2.0 结构](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-structure)

    Examples:
        >>> d = Card().header("Report", template="blue").markdown("**hi**").to_dict()
        >>> d["schema"], d["header"]["template"]
        ('2.0', 'blue')
        >>> d["body"]["elements"]
        [{'tag': 'markdown', 'content': '**hi**'}]
        >>> Card().to_dict()
        {'schema': '2.0', 'body': {'elements': []}}
    """

    def __init__(self) -> None:
        self._header: dict[str, Any] | None = None
        self._config: dict[str, Any] = {}
        self._elements: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Header / config
    # ------------------------------------------------------------------

    def header(
        self,
        title: str,
        *,
        subtitle: str | None = None,
        template: str = "blue",
        icon: dict[str, Any] | None = None,
        tags: list[dict[str, Any]] | None = None,
    ) -> Card:
        r"""
        设置卡片标题栏，并返回自身以便链式调用。

        Args:
            title: 标题文案。
            subtitle: 副标题文案。
            template: 标题栏颜色主题，会经 [feishu.cards.validation.validate_template][] 校验。
            icon: 标题栏图标配置。
            tags: 标题栏右侧的文本标签列表（对应 `text_tag_list`）。

        Returns:
            当前 [feishu.cards.builder.Card][] 实例。

        飞书文档:
            [标题组件](https://open.feishu.cn/document/feishu-cards/card-json-v2-components/content-components/title)

        Examples:
            >>> hdr = Card().header("Title", subtitle="sub", template="green").to_dict()["header"]
            >>> hdr["title"], hdr["template"]
            ({'tag': 'plain_text', 'content': 'Title'}, 'green')
            >>> hdr["subtitle"]
            {'tag': 'plain_text', 'content': 'sub'}
        """
        hdr: dict[str, Any] = {
            "title": elements._plain_text(title),
            "template": validate_template(template),
        }
        if subtitle is not None:
            hdr["subtitle"] = elements._plain_text(subtitle)
        if icon is not None:
            hdr["icon"] = icon
        if tags is not None:
            hdr["text_tag_list"] = tags
        self._header = hdr
        return self

    def config(self, **opts: Any) -> Card:
        r"""
        将关键字参数合并进卡片的全局配置（如 `streaming_mode`、`width_mode` 等），并返回自身。

        Args:
            **opts: 待合并进卡片 `config` 的配置项。

        Returns:
            当前 [feishu.cards.builder.Card][] 实例。

        飞书文档:
            [卡片全局配置](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-structure)

        Examples:
            >>> Card().config(width_mode="fill").to_dict()["config"]
            {'width_mode': 'fill'}
        """
        self._config.update(opts)
        return self

    # ------------------------------------------------------------------
    # Element appenders — all delegate to elements.* (Task 3)
    # ------------------------------------------------------------------

    def markdown(
        self,
        content: str,
        *,
        text_align: str | None = None,
        text_size: str | None = None,
        escape: bool = False,
        element_id: str | None = None,
    ) -> Card:
        r"""
        追加一个 markdown 元素（委托给 [feishu.cards.elements.md][]），并返回自身。

        Args:
            content: markdown 文本内容。
            text_align: 文本对齐方式，如 `left`、`center`、`right`。
            text_size: 文本字号，如 `normal`、`heading`。
            escape: 是否先对 `content` 转义控制字符。
            element_id: 元素的自定义 ID，会经 [feishu.cards.validation.validate_element_id][] 校验。

        Returns:
            当前 [feishu.cards.builder.Card][] 实例。

        Examples:
            >>> Card().markdown("**bold**", text_align="center").to_dict()["body"]["elements"][0]
            {'tag': 'markdown', 'content': '**bold**', 'text_align': 'center'}
        """
        self._elements.append(
            elements.md(
                content,
                text_align=text_align,
                text_size=text_size,
                escape=escape,
                element_id=element_id,
            )
        )
        return self

    def text(self, content: str) -> Card:
        r"""
        追加纯文本（不转义）的便捷方法，等价于 [feishu.cards.builder.Card.markdown][]。

        Args:
            content: 文本内容。

        Returns:
            当前 [feishu.cards.builder.Card][] 实例。

        Examples:
            >>> Card().text("plain").to_dict()["body"]["elements"][0]
            {'tag': 'markdown', 'content': 'plain'}
        """
        return self.markdown(content)

    def divider(self) -> Card:
        r"""
        追加一条分割线元素（委托给 [feishu.cards.elements.hr][]），并返回自身。

        Returns:
            当前 [feishu.cards.builder.Card][] 实例。

        Examples:
            >>> Card().divider().to_dict()["body"]["elements"][0]
            {'tag': 'hr'}
        """
        self._elements.append(elements.hr())
        return self

    def image(self, img_key: str, alt: str, **opts: Any) -> Card:
        r"""
        追加一个图片元素（委托给 [feishu.cards.elements.img][]），并返回自身。

        Args:
            img_key: 图片的 key，通过上传图片接口获取。
            alt: 图片的悬浮提示文案（无障碍替代文本）。
            **opts: 其余原样透传给图片元素的字段，如 `scale_type`、`element_id` 等。

        Returns:
            当前 [feishu.cards.builder.Card][] 实例。

        Examples:
            >>> Card().image("img_1", "alt").to_dict()["body"]["elements"][0]["tag"]
            'img'
        """
        self._elements.append(elements.img(img_key, alt, **opts))
        return self

    def button(
        self,
        text: str,
        *,
        value: dict[str, Any] | None = None,
        url: str | None = None,
        type: str = "default",  # noqa: A002 - matches Feishu field name
        confirm: dict[str, Any] | None = None,
        icon: dict[str, Any] | None = None,
        element_id: str | None = None,
    ) -> Card:
        r"""
        追加一个按钮元素（委托给 [feishu.cards.elements.button][]），并返回自身。

        Args:
            text: 按钮文案。
            value: 点击回调时回传的业务数据，生成 `callback` 行为。
            url: 点击时跳转的链接，生成 `open_url` 行为。
            type: 按钮样式类型，如 `default`、`primary`、`danger`。
            confirm: 点击前的二次确认弹窗配置。
            icon: 按钮图标配置。
            element_id: 元素的自定义 ID，会经 [feishu.cards.validation.validate_element_id][] 校验。

        Returns:
            当前 [feishu.cards.builder.Card][] 实例。

        Examples:
            >>> Card().button("Go", value={"x": 1}).to_dict()["body"]["elements"][0]["behaviors"]
            [{'type': 'callback', 'value': {'x': 1}}]
        """
        self._elements.append(
            elements.button(
                text,
                value=value,
                url=url,
                type=type,
                confirm=confirm,
                icon=icon,
                element_id=element_id,
            )
        )
        return self

    def columns(
        self,
        *cols: ColumnSet | dict[str, Any],
        flex_mode: str = "none",
        horizontal_spacing: str | int | None = None,
        **opts: Any,
    ) -> Card:
        r"""
        追加一个分栏（column_set）元素，并返回自身。

        当仅传入单个 [feishu.cards.builder.ColumnSet][] 时，直接调用其
        [feishu.cards.builder.ColumnSet.to_dict][] 追加；此时以该 `ColumnSet` 自身的
        `flex_mode`/间距为准，调用方传入的关键字参数不会生效（此优先级为有意设计）。

        否则（零个、两个及以上参数，或单个非 `ColumnSet` 参数），每个参数都必须是原始的
        列字典（`{"tag": "column", ...}`）。它们会被包裹进经 [feishu.cards.elements.column_set][]
        构造的同一个分栏中，此时调用方的 `flex_mode`、`horizontal_spacing` 及额外的 `**opts`
        均会生效。在该路径上传入 `ColumnSet` 实例会抛出 `TypeError`，以避免静默生成结构错误的嵌套分栏。

        Args:
            *cols: 单个 [feishu.cards.builder.ColumnSet][]，或一组原始列字典。
            flex_mode: 列在窄屏下的自适应方式（仅在原始列字典路径生效）。
            horizontal_spacing: 列间水平间距（仅在原始列字典路径生效）。
            **opts: 其余原样透传给分栏字典的字段（仅在原始列字典路径生效）。

        Returns:
            当前 [feishu.cards.builder.Card][] 实例。

        Raises:
            TypeError: 在原始列字典路径上传入了 [feishu.cards.builder.ColumnSet][] 实例时抛出。

        飞书文档:
            [多列布局组件](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/containers/column-set)

        Examples:
            >>> col = {"tag": "column", "width": "auto", "elements": []}
            >>> Card().columns(col, col, flex_mode="stretch").to_dict()["body"]["elements"][0]["flex_mode"]
            'stretch'
        """
        if len(cols) == 1 and isinstance(cols[0], ColumnSet):
            self._elements.append(cols[0].to_dict())
            return self
        for c in cols:
            if isinstance(c, ColumnSet):
                raise TypeError(
                    "columns() accepts a single ColumnSet, or raw column dicts; "
                    "to add multiple column_sets, call .columns()/.column_set() once per column_set"
                )
        self._elements.append(
            elements.column_set(
                cast(list[dict[str, Any]], list(cols)),
                flex_mode=flex_mode,
                horizontal_spacing=horizontal_spacing,
                **opts,
            )
        )
        return self

    def column_set(self) -> ColumnSet:
        r"""
        返回一个绑定到本卡片的全新 [feishu.cards.builder.ColumnSet][] 子构造器。

        在返回对象上链式调用 [feishu.cards.builder.ColumnSet.column][] 逐列添加内容，
        再调用 [feishu.cards.builder.ColumnSet.end][] 将分栏追加回本卡片并取回本卡片。

        Returns:
            绑定到本卡片的 [feishu.cards.builder.ColumnSet][] 实例。

        Examples:
            >>> card = Card()
            >>> card.column_set().column({"tag": "markdown", "content": "a"}).end() is card
            True
            >>> card.to_dict()["body"]["elements"][0]["tag"]
            'column_set'
        """
        return ColumnSet(parent=self)

    def add(self, raw: dict[str, Any]) -> Card:
        r"""
        逃生通道：原样追加任意元素字典，并返回自身。

        Args:
            raw: 待追加的原始元素字典。

        Returns:
            当前 [feishu.cards.builder.Card][] 实例。

        Examples:
            >>> Card().add({"tag": "custom_thing", "k": 1}).to_dict()["body"]["elements"]
            [{'tag': 'custom_thing', 'k': 1}]
        """
        self._elements.append(raw)
        return self

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        r"""
        生成卡片 2.0 的骨架字典。

        包含的键：

        - `"schema": "2.0"`：始终存在。
        - `"config": {...}`：仅在至少设置过一项配置时出现。
        - `"header": {...}`：仅在调用过 [feishu.cards.builder.Card.header][] 时出现。
        - `"body": {"elements": [...]}`：始终存在（元素列表可能为空）。

        别名 `build` 与本方法等价。

        Returns:
            卡片 2.0 的字典表示。

        飞书文档:
            [卡片 JSON 2.0 结构](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-structure)

        Examples:
            >>> Card().to_dict()
            {'schema': '2.0', 'body': {'elements': []}}
            >>> c = Card().text("hi")
            >>> c.build() == c.to_dict()
            True
        """
        out: dict[str, Any] = {"schema": "2.0"}
        if self._config:
            out["config"] = dict(self._config)
        if self._header is not None:
            out["header"] = self._header
        out["body"] = {"elements": list(self._elements)}
        return out

    build = to_dict
