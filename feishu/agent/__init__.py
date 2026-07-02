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

`feishu.agent` 只导出高频核心原语：`Agent`、工具注册表、工具定义与工具结果。模型消息类型、审批引擎、
持久化 store、OAuth、prompting、summarization、toolkit 工厂与 bundle 装配器都从各自概念模块导入，例如
`feishu.agent.llm`、`feishu.agent.approval`、`feishu.agent.persistence`、`feishu.agent.toolkit.calendar` 与
`feishu.agent.bundles`。

SDK 的契约是 primitives-only：它提供可组合的运行时部件，不提供单一默认 `build_agent()`。产品应显式选择
backend、store、approval engine、tool registry、OAuth 与 UI policy 后自行装配，这样授权、持久化、安全策略与
产品体验不会被隐藏在一套默认配置里。
"""

from __future__ import annotations

from .loop import Agent
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
