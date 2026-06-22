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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..events.dispatcher import EventDispatcher
    from .loop import Agent


def register_agent(
    dispatcher: EventDispatcher,
    agent: Agent,
    *,
    message_event: str = "im.message.receive_v1",
    card_event: str = "card.action.trigger",
) -> None:
    r"""
    将智能体的消息处理与卡片回调挂载到事件分发器上。

    把 [feishu.agent.loop.Agent.run][] 注册为消息事件的处理函数，把
    [feishu.agent.loop.Agent.handle_card_action][] 注册为卡片回调事件的处理函数。`dispatcher` 须提供与
    [feishu.events.dispatcher.EventDispatcher][] 一致的 `on(event_type)` 装饰器接口。

    Args:
        dispatcher: 事件分发器，须提供 `on(event_type)` 装饰器接口。
        agent: 已构造的 [feishu.agent.loop.Agent][]。
        message_event: 消息事件类型。默认为 `im.message.receive_v1`。
        card_event: 卡片回调事件类型。默认为 `card.action.trigger`。

    飞书文档:
        [接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)

        [卡片回传交互](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-callback-communication)

    Examples:
        >>> register_agent(dispatcher, agent)  # doctest:+SKIP
    """
    dispatcher.on(message_event)(agent.run)
    dispatcher.on(card_event)(agent.handle_card_action)
