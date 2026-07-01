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

from .approval import (
    ApprovalEngine,
    ApprovalOutcome,
    ApprovalStatus,
    AuditLog,
    DefaultApprovalEngine,
    ExecutionResultStore,
)
from .context import ToolContext, current_tool_context, use_tool_context
from .dispatch import register_agent
from .integrity import (
    derive_approval_id,
    derive_idempotency_key,
    payload_sha256,
    payload_summary,
    stable_hash,
)
from .llm import (
    ContentPart,
    LlmBackend,
    Message,
    MessageStop,
    ReasoningDelta,
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
from .payment_accounts import (
    PaymentAccount,
    PaymentAccountResolver,
    payment_account_handle,
    payment_account_keys,
)
from .persistence import (
    JsonlAuditLog,
    SqliteExecutionResultStore,
    SqlitePendingApprovalStore,
    SqliteSessionStore,
)
from .result import ToolOutcome, ToolResult
from .session import (
    ClaimResult,
    InMemoryPendingApprovalStore,
    InMemorySessionStore,
    PendingApproval,
    PendingApprovalStore,
    SessionStore,
)
from .shared_files import (
    InMemorySharedFileStore,
    SharedFile,
    SharedFileResolver,
    SharedFileStore,
    SqliteSharedFileStore,
    shared_file_keys,
)
from .streaming import stream_text
from .toolkit import (
    append_to_document,
    append_to_sheet,
    approve_approval_task,
    book_meeting_room,
    cancel_approval_instance,
    cancel_calendar_event,
    cancel_reservation,
    comment_on_task,
    create_approval_instance,
    create_bitable_record,
    create_calendar_event,
    create_document,
    create_task,
    delete_bitable_record,
    delete_document,
    delete_sheet_rows,
    delete_task,
    delete_task_comment,
    describe_shared_file,
    find_user,
    get_approval_definition,
    get_approval_status,
    get_document_content,
    get_meeting_record,
    get_message_thread,
    list_approval_definitions,
    list_bitable_records,
    list_calendar_events,
    list_document_blocks,
    list_meeting_room_buildings,
    list_my_payment_accounts,
    list_my_pending_approvals,
    list_my_tasks,
    list_shared_files,
    list_task_comments,
    list_whiteboard_nodes,
    query_calendar_freebusy,
    query_meeting_room_freebusy,
    read_sheet_range,
    reject_approval_task,
    reserve_meeting,
    respond_to_invite,
    search_documents,
    search_meeting_rooms,
    update_bitable_record,
    update_calendar_event,
    update_document,
    update_reservation,
    update_sheet_range,
    update_task,
    update_task_comment,
    upload_shared_file_to_drive,
)
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
    "ReasoningDelta",
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
    "ClaimResult",
    "Agent",
    "StreamResult",
    "accumulate_stream",
    "session_id_for",
    "user_message_from_event",
    "register_agent",
    "stream_text",
    # Result envelope
    "ToolOutcome",
    "ToolResult",
    # Human-in-the-loop approval engine
    "ApprovalEngine",
    "ApprovalOutcome",
    "ApprovalStatus",
    "DefaultApprovalEngine",
    "ExecutionResultStore",
    "AuditLog",
    # Durable default stores (SQLite / JSONL)
    "SqliteSessionStore",
    "SqlitePendingApprovalStore",
    "SqliteExecutionResultStore",
    "JsonlAuditLog",
    # Shared user files (opaque handles; bytes never at rest by default)
    "SharedFile",
    "SharedFileStore",
    "InMemorySharedFileStore",
    "SqliteSharedFileStore",
    "SharedFileResolver",
    "shared_file_keys",
    # Payment accounts (opaque handles, values in memory only, self-scoped)
    "PaymentAccount",
    "PaymentAccountResolver",
    "payment_account_handle",
    "payment_account_keys",
    # Integrity / idempotency helpers
    "stable_hash",
    "payload_sha256",
    "payload_summary",
    "derive_approval_id",
    "derive_idempotency_key",
    # Tool context
    "ToolContext",
    "current_tool_context",
    "use_tool_context",
    # Tool library factories
    "list_calendar_events",
    "query_calendar_freebusy",
    "create_calendar_event",
    "update_calendar_event",
    "cancel_calendar_event",
    "respond_to_invite",
    "list_meeting_room_buildings",
    "search_meeting_rooms",
    "query_meeting_room_freebusy",
    "book_meeting_room",
    "search_documents",
    "get_document_content",
    "get_message_thread",
    "get_meeting_record",
    "create_task",
    "create_bitable_record",
    "list_approval_definitions",
    "get_approval_definition",
    "create_approval_instance",
    "get_approval_status",
    "approve_approval_task",
    "reject_approval_task",
    "list_my_pending_approvals",
    "list_my_payment_accounts",
    # tier 2/3
    "reserve_meeting",
    "create_document",
    "append_to_document",
    "list_document_blocks",
    "append_to_sheet",
    "read_sheet_range",
    "find_user",
    "list_whiteboard_nodes",
    "comment_on_task",
    # full create+update+delete lifecycle
    "update_task",
    "delete_task",
    "list_my_tasks",
    "update_task_comment",
    "delete_task_comment",
    "list_task_comments",
    "cancel_approval_instance",
    "update_reservation",
    "cancel_reservation",
    "update_bitable_record",
    "delete_bitable_record",
    "list_bitable_records",
    "update_document",
    "delete_document",
    "update_sheet_range",
    "delete_sheet_rows",
    # shared user files
    "list_shared_files",
    "describe_shared_file",
    "upload_shared_file_to_drive",
]
