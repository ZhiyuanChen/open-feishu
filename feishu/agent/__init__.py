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

r"""
智能体运行时的核心入口。

`Agent(config)` 是开箱即用的应用入口；需要手工装配或产品继承时，可使用底层
`feishu.agent.loop.AgentEngine`。模型消息类型、审批引擎、持久化 store、OAuth、prompting、summarization、
toolkit 工厂与 bundle 装配器仍从各自概念模块导入。
"""

from __future__ import annotations

from .app import Agent
from .result import ToolOutcome, ToolResult
from .tools import Tool, ToolRegistry, ToolValidationError

__all__ = [
    "Agent",
    "Tool",
    "ToolRegistry",
    "ToolValidationError",
    "ToolOutcome",
    "ToolResult",
]
