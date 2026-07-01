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

from .registration import register_agent
from .llm import (
    ContentPart,
    LlmBackend,
    Message,
    MessageStop,
    Role,
    StopReason,
    StreamChunk,
    TextDelta,
    TextPart,
    ToolCall,
    ToolCallDelta,
    ToolResultPart,
    ToolSpec,
    ToolUsePart,
)
from .loop import Agent, StreamResult, accumulate_stream, session_id_for, user_message_from_event
from .session import (
    InMemoryPendingApprovalStore,
    InMemorySessionStore,
    PendingApproval,
    PendingApprovalStore,
    SessionStore,
)
from .streaming import stream_text
from .tools import Tool, ToolRegistry, ToolValidationError

__all__ = [
    "LlmBackend",
    "Message",
    "TextPart",
    "ToolUsePart",
    "ToolResultPart",
    "ContentPart",
    "ToolSpec",
    "Role",
    "StopReason",
    "TextDelta",
    "ToolCallDelta",
    "MessageStop",
    "StreamChunk",
    "ToolCall",
    "Tool",
    "ToolRegistry",
    "ToolValidationError",
    "SessionStore",
    "InMemorySessionStore",
    "PendingApproval",
    "PendingApprovalStore",
    "InMemoryPendingApprovalStore",
    "Agent",
    "StreamResult",
    "accumulate_stream",
    "session_id_for",
    "user_message_from_event",
    "register_agent",
    "stream_text",
]
