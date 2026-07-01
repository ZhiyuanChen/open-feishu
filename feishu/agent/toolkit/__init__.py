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
飞书工具库：按产品分模块的参数化工具工厂，与产品文案 / 选择解耦。

每个工厂封装一个通用飞书操作（列日程、建任务、查文档……），把工具描述、`name`、`locale`、是否需要审批、
是否以用户身份执行、所需授权范围等作为参数注入，并产出一个 [feishu.agent.tools.Tool][]。处理函数经
[feishu.agent.context.current_tool_context][] 读取按轮上下文以拿到（用户态）飞书客户端，机器人只要
「选择 + 覆盖文案」即可注册。

审批语义：把 `requires_approval=True` 的工具交给 [feishu.agent.loop.Agent][] 后，模型发起调用会先挂起并发出
审批卡片；用户批准后处理函数才真正执行——故写类工具的处理函数直接执行写操作，提议由 `requires_approval` 驱动。
"""

from __future__ import annotations

from .approvals import (
    approve_approval_task,
    cancel_approval_instance,
    create_approval_instance,
    get_approval_definition,
    get_approval_status,
    list_approval_definitions,
    list_my_payment_accounts,
    list_my_pending_approvals,
    reject_approval_task,
)
from .bitable import (
    create_bitable_record,
    delete_bitable_record,
    list_bitable_records,
    update_bitable_record,
)
from .calendar import (
    cancel_calendar_event,
    create_calendar_event,
    list_calendar_events,
    query_calendar_freebusy,
    respond_to_invite,
    update_calendar_event,
)
from .contacts import find_user
from .content import (
    append_to_document,
    append_to_sheet,
    create_document,
    delete_document,
    delete_sheet_rows,
    list_document_blocks,
    read_sheet_range,
    update_document,
    update_sheet_range,
)
from .documents import get_document_content, get_meeting_record, get_message_thread, search_documents
from .rooms import (
    book_meeting_room,
    list_meeting_room_buildings,
    query_meeting_room_freebusy,
    search_meeting_rooms,
)
from .shared_files import describe_shared_file, list_shared_files, upload_shared_file_to_drive
from .tasks import (
    comment_on_task,
    create_task,
    delete_task,
    delete_task_comment,
    list_my_tasks,
    list_task_comments,
    update_task,
    update_task_comment,
)
from .vc import cancel_reservation, reserve_meeting, update_reservation
from .whiteboard import list_whiteboard_nodes

__all__ = [
    # calendar
    "list_calendar_events",
    "query_calendar_freebusy",
    "create_calendar_event",
    "update_calendar_event",
    "cancel_calendar_event",
    "respond_to_invite",
    # meeting rooms
    "list_meeting_room_buildings",
    "search_meeting_rooms",
    "query_meeting_room_freebusy",
    "book_meeting_room",
    # documents (reads; the agent summarizes)
    "search_documents",
    "get_document_content",
    "get_message_thread",
    "get_meeting_record",
    # tasks
    "create_task",
    # bitable
    "create_bitable_record",
    # approvals
    "list_approval_definitions",
    "get_approval_definition",
    "create_approval_instance",
    "get_approval_status",
    "approve_approval_task",
    "reject_approval_task",
    "list_my_pending_approvals",
    "list_my_payment_accounts",
    # tier 2/3: vc, content, people, whiteboard, tasks
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
