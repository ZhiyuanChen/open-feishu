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

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ToolOutcome(str, Enum):
    r"""
    工具执行结果的归一化处置（disposition）。

    工具处理函数既可直接返回任意值（视同 `COMPLETED`），也可返回一个 [feishu.agent.result.ToolResult][]
    以显式声明处置。`NEEDS_USER_AUTH` 要求先引导用户完成 OAuth 授权：[feishu.agent.loop.Agent][] 据此发送一张
    授权卡片并挂起本轮，待 OAuth callback 完成后恢复原工具调用。写操作的审批由工具的 `requires_approval` 在
    分发**之前**统一挂起（见 [feishu.agent.tools.Tool][]），不经由此枚举。由于继承自 `str`，枚举成员可直接与
    字符串字面量比较。

    Examples:
        >>> ToolOutcome.NEEDS_USER_AUTH == "needs_user_auth"
        True
        >>> ToolOutcome("completed") is ToolOutcome.COMPLETED
        True
    """

    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_USER_AUTH = "needs_user_auth"
    CANCELLED = "cancelled"
    INFORMATIONAL = "informational"


@dataclass(slots=True)
class ToolResult:
    r"""
    工具处理函数的结构化返回值，向 [feishu.agent.loop.Agent][] 声明处置与回传内容。

    多数工具可直接返回原始值；需要声明处置时返回本类型：`NEEDS_USER_AUTH` 可随附 `auth_scopes` 与
    `authorize_url`（旧式回退链接）。`content` 是回传给模型的工具结果文本/对象，`is_error` 为 `True` 时模型
    据此调整后续行为。写操作的二次确认由工具的 `requires_approval` 在分发前统一处理，不经由本返回值。

    Examples:
        >>> ok = ToolResult(outcome=ToolOutcome.COMPLETED, content="已列出 3 个日程")
        >>> ok.outcome
        <ToolOutcome.COMPLETED: 'completed'>
        >>> ok.is_error
        False
    """

    outcome: ToolOutcome
    content: Any = None
    authorize_url: str | None = None
    auth_scopes: tuple[str, ...] = ()
    is_error: bool = False
