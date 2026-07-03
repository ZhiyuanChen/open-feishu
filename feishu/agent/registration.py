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

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..events.dispatcher import EventDispatcher
    from ..events.idempotency import SeenStore
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


def create_agent_dispatcher(
    agent: Agent,
    *,
    seen_store: SeenStore | None = None,
    seen_path: str | Path | None = None,
    message_event: str = "im.message.receive_v1",
    card_event: str = "card.action.trigger",
) -> EventDispatcher:
    r"""
    创建 [feishu.events.dispatcher.EventDispatcher][] 并把 agent 绑定到消息与卡片事件上。

    Args:
        agent: 接收消息与卡片事件的 [feishu.agent.loop.Agent][]。
        seen_store: 事件幂等存储；为空时不去重，除非提供 `seen_path`。
        seen_path: 可选 JSON 文件路径，用于构造 [feishu.events.idempotency.FileSeenStore][]；
            适合单进程机器人在重启后继续去重。
        message_event: 路由到 [feishu.agent.loop.Agent.run][] 的消息事件类型。
        card_event: 路由到 [feishu.agent.loop.Agent.handle_card_action][] 的卡片回调事件类型。

    Returns:
        已完成 agent 事件绑定的 dispatcher。
    """
    from ..events.dispatcher import EventDispatcher
    from ..events.idempotency import FileSeenStore

    resolved_seen_store = FileSeenStore(seen_path) if seen_store is None and seen_path is not None else seen_store
    dispatcher = EventDispatcher(seen_store=resolved_seen_store)
    register_agent(dispatcher, agent, message_event=message_event, card_event=card_event)
    return dispatcher
