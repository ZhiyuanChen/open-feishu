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

from collections.abc import Mapping
from typing import Any


def _node_from(event: Any) -> Mapping[str, Any]:
    """Resolve the action-bearing node from a dict / Event-like object.

    Accepts: a full event payload (has top-level 'event'), a bare unwrapped
    node, or an object exposing a mapping '.body'.
    """
    if isinstance(event, Mapping):
        inner = event.get("event")
        return inner if isinstance(inner, Mapping) else event
    body = getattr(event, "body", None)
    if isinstance(body, Mapping):
        return body
    raise TypeError(f"cannot extract card action node from {type(event).__name__}")


class CardAction:
    r"""
    `card.action.trigger` 事件的类型化只读视图。

    将卡片交互回调事件包装为带类型属性的对象，便于读取触发者、回传值、表单值以及
    更新卡片所需的 `token` 等字段。可接收完整事件载荷、已解包的事件节点，或暴露
    `.body` 映射的事件对象。通常通过 [feishu.cards.callback.parse_action][] 构造。

    飞书文档:
        [卡片回传交互](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-callback-communication)

    Examples:
        >>> event = {
        ...     "event": {
        ...         "operator": {"open_id": "ou_1", "user_id": "u1", "union_id": "on_1"},
        ...         "token": "c-update-token",
        ...         "action": {"value": {"decision": "approve"}, "tag": "button", "name": "approve"},
        ...         "context": {"open_message_id": "om_1", "open_chat_id": "oc_1"},
        ...     }
        ... }
        >>> action = CardAction(event)
        >>> action.value
        {'decision': 'approve'}
        >>> action.open_id, action.message_id, action.token
        ('ou_1', 'om_1', 'c-update-token')
    """

    __slots__ = ("_node", "_action", "_operator", "_context", "raw")

    def __init__(self, event: Any) -> None:
        self.raw = event
        self._node = _node_from(event)
        action = self._node.get("action")
        self._action: Mapping[str, Any] = action if isinstance(action, Mapping) else {}
        operator = self._node.get("operator")
        self._operator: Mapping[str, Any] = operator if isinstance(operator, Mapping) else {}
        context = self._node.get("context")
        self._context: Mapping[str, Any] = context if isinstance(context, Mapping) else {}

    @property
    def value(self) -> dict[str, Any]:
        r"""按钮等组件回传的业务数据；缺失时返回空字典。"""
        v = self._action.get("value")
        return dict(v) if isinstance(v, Mapping) else {}

    @property
    def operator(self) -> dict[str, Any]:
        r"""触发者的 ID 集合，仅含存在的 `open_id`、`user_id`、`union_id` 字段。"""
        out: dict[str, Any] = {}
        for key in ("open_id", "user_id", "union_id"):
            if key in self._operator:
                out[key] = self._operator[key]
        return out

    @property
    def open_id(self) -> str | None:
        r"""触发者的 `open_id`；缺失时返回 `None`。"""
        return self._operator.get("open_id")

    @property
    def user_id(self) -> str | None:
        r"""触发者的 `user_id`；缺失时返回 `None`。"""
        return self._operator.get("user_id")

    @property
    def union_id(self) -> str | None:
        r"""触发者的 `union_id`；缺失时返回 `None`。"""
        return self._operator.get("union_id")

    @property
    def token(self) -> str | None:
        r"""
        更新卡片所需的凭证（有效期 30 分钟、最多更新 2 次），并非 Webhook 校验 token。

        飞书文档:
            [延时更新卡片](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-callback-communication/delay-update-card)
        """
        return self._node.get("token")

    @property
    def message_id(self) -> str | None:
        r"""承载该卡片的消息 ID（`open_message_id`）；缺失时返回 `None`。"""
        return self._context.get("open_message_id")

    @property
    def chat_id(self) -> str | None:
        r"""承载该卡片的会话 ID（`open_chat_id`）；缺失时返回 `None`。"""
        return self._context.get("open_chat_id")

    @property
    def tag(self) -> str | None:
        r"""触发交互的组件标签，如 `button`；缺失时返回 `None`。"""
        return self._action.get("tag")

    @property
    def name(self) -> str | None:
        r"""触发交互的组件名称；缺失时返回 `None`。"""
        return self._action.get("name")

    @property
    def form_value(self) -> dict[str, Any]:
        r"""表单容器提交时回传的各字段取值；缺失时返回空字典。"""
        fv = self._action.get("form_value")
        return dict(fv) if isinstance(fv, Mapping) else {}


def parse_action(event: Any) -> CardAction:
    r"""
    将 `card.action.trigger` 事件解析为类型化的 [feishu.cards.callback.CardAction][] 视图。

    Args:
        event: 卡片交互回调事件，可为完整事件载荷、已解包的事件节点，或暴露 `.body` 映射的事件对象。

    Returns:
        包装该事件的 [feishu.cards.callback.CardAction][] 实例。

    Raises:
        TypeError: 当无法从 `event` 中提取卡片交互节点时抛出。

    飞书文档:
        [卡片回传交互](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-callback-communication)

    Examples:
        >>> action = parse_action({"event": {"action": {"value": {"decision": "approve"}}}})
        >>> action.value
        {'decision': 'approve'}
        >>> parse_action({"event": {"action": {}}}).tag is None
        True
    """
    return CardAction(event)
