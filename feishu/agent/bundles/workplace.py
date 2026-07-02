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

from collections.abc import Callable
from typing import Any

from ..toolkit._base import reauth_on_permission_error
from ..toolkit.approvals import (
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
from ..toolkit.bitable import create_bitable_record, delete_bitable_record, list_bitable_records, update_bitable_record
from ..toolkit.calendar import (
    cancel_calendar_event,
    create_calendar_event,
    list_calendar_events,
    query_calendar_freebusy,
    respond_to_invite,
    update_calendar_event,
)
from ..toolkit.contacts import find_user
from ..toolkit.content import (
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
from ..toolkit.documents import get_document_content, get_meeting_record, get_message_thread, search_documents
from ..toolkit.mail import (
    get_mail_message,
    list_mail_folders,
    list_mail_messages,
    search_mail_messages,
    send_mail_message,
    summarize_mail_message,
    summarize_mail_messages,
)
from ..toolkit.rooms import (
    book_meeting_room,
    list_meeting_room_buildings,
    list_meeting_rooms,
    query_meeting_room_freebusy,
)
from ..toolkit.shared_files import describe_shared_file, list_shared_files, upload_shared_file_to_drive
from ..toolkit.tasks import (
    comment_on_task,
    create_task,
    delete_task,
    delete_task_comment,
    list_my_tasks,
    list_task_comments,
    update_task,
    update_task_comment,
)
from ..toolkit.vc import cancel_reservation, reserve_meeting, update_reservation
from ..toolkit.whiteboard import list_whiteboard_nodes
from ..tools import Tool, ToolRegistry
from .registry import BUNDLES, BundleContext

# Feishu permission scopes the user must grant per capability (tune to your app's configured scopes).
CALENDAR_READ_SCOPES = ("calendar:calendar:readonly",)
CALENDAR_SCOPES = ("calendar:calendar", *CALENDAR_READ_SCOPES)
ROOM_SCOPES = ("calendar:room:readonly",)
# Valid, app-enabled identifiers (colon form). The legacy `docs:document.readonly`-style names are
# rejected by the authorize page (error 20043). These were live-confirmed on the user's token.
DOC_READ_SCOPES = ("docx:document", "drive:drive", "wiki:wiki")
TASK_SCOPES = ("task:task",)
BITABLE_SCOPES = ("bitable:app",)
APPROVAL_SCOPES = ("approval:approval",)
VC_SCOPES = ("vc:reserve",)
DOC_WRITE_SCOPES = ("docx:document",)
DRIVE_SCOPES = ("drive:drive",)
SHEET_SCOPES = ("sheets:spreadsheet",)
CONTACT_SCOPES = ("contact:user:search",)
BOARD_SCOPES = ("board:whiteboard:node:read",)
TASK_COMMENT_SCOPES = ("task:comment:write",)
MAIL_READ_SCOPES = (
    "mail:user_mailbox.message:readonly",
    "mail:user_mailbox.message.subject:read",
    "mail:user_mailbox.message.body:read",
    "mail:user_mailbox.message.address:read",
)
MAIL_SEND_SCOPES = ("mail:user_mailbox.message:send",)
MAIL_FOLDER_READ_SCOPES = ("mail:user_mailbox.folder:read",)


def _build_workplace_tool_registry(
    *,
    registry: ToolRegistry | None = None,
    describe_analyzer: Callable[..., Any] | None = None,
    text_summarizer: Callable[..., Any] | None = None,
    locale: str = "zh-CN",
    timezone: str = "Asia/Shanghai",
    mail_summary_max_messages: int = 10,
    mail_summary_max_body_chars: int = 4000,
    mail_summary_max_chars: int = 2000,
) -> ToolRegistry:
    r"""把由原子飞书工具工厂组成的默认办公 bundle 注册进工具表。"""
    registry = registry or ToolRegistry()
    add = registry.add

    def add_scoped(tool: Tool) -> Tool:
        return add(reauth_on_permission_error(tool, tool.auth_scopes))

    # Calendar
    add_scoped(
        list_calendar_events(
            description="查询我的日程安排；我会以你的身份读取你的日历。",
            auth_scopes=CALENDAR_SCOPES,
            timezone=timezone,
        )
    )
    add_scoped(
        query_calendar_freebusy(
            description="查询某人或某会议室在一段时间内的忙闲情况。",
            auth_scopes=CALENDAR_SCOPES,
            timezone=timezone,
        )
    )
    add_scoped(
        create_calendar_event(
            description="在你的日历上创建一个日程；执行前我会先发确认卡片。",
            auth_scopes=CALENDAR_SCOPES,
            timezone=timezone,
        )
    )
    add_scoped(
        update_calendar_event(
            description="改期或编辑我的某个日程（按 event_id，只改你给到的字段）；执行前我会先发确认卡片。",
            auth_scopes=CALENDAR_SCOPES,
            timezone=timezone,
        )
    )
    add_scoped(
        cancel_calendar_event(
            description="取消（删除）我的某个日程（按 event_id）；执行前我会先发确认卡片。",
            auth_scopes=CALENDAR_SCOPES,
        )
    )
    add_scoped(
        respond_to_invite(
            description="回复一个日程邀请：接受 / 待定 / 拒绝（按 event_id）；执行前我会先发确认卡片。",
            auth_scopes=CALENDAR_SCOPES,
        )
    )

    # Meeting rooms
    add(
        list_meeting_room_buildings(
            description="列出会议室所在的建筑/楼宇（先确定建筑，再在其中查会议室）。",
            auth_scopes=ROOM_SCOPES,
        )
    )
    add(list_meeting_rooms(description="按名称或容量搜索可用的会议室。", auth_scopes=ROOM_SCOPES))
    add(query_meeting_room_freebusy(description="查询某个会议室的忙闲情况。", auth_scopes=ROOM_SCOPES))
    add_scoped(
        book_meeting_room(
            description="预订一个会议室（作为日程资源）；执行前我会先发确认卡片。",
            auth_scopes=CALENDAR_SCOPES,
            timezone=timezone,
        )
    )

    # Documents (reads - the agent summarizes in the reply)
    add(search_documents(description="搜索你有权限访问的云文档与知识库。", auth_scopes=DOC_READ_SCOPES))
    add(
        get_document_content(
            description="读取一个飞书文档的内容（之后由我来帮你总结）。",
            auth_scopes=DOC_READ_SCOPES,
        )
    )
    add(
        get_message_thread(
            description="读取一条消息所在的回复串（之后由我来帮你总结）。",
            auth_scopes=DOC_READ_SCOPES,
        )
    )
    add(
        get_meeting_record(
            description="读取一次会议的纪要或记录（之后由我来帮你总结）。",
            auth_scopes=DOC_READ_SCOPES,
            timezone=timezone,
        )
    )

    # Tasks
    add(create_task(description="创建一个任务；执行前我会先发确认卡片。", auth_scopes=TASK_SCOPES))
    add(
        list_my_tasks(
            description="列出你负责的任务（只看你本人的任务，可按是否完成筛选）。",
            auth_scopes=TASK_SCOPES,
        )
    )
    add(
        update_task(
            description="修改某个任务的标题或描述（按 task_guid，只改你给到的字段）；执行前我会先发确认卡片。",
            auth_scopes=TASK_SCOPES,
        )
    )
    add(
        delete_task(
            description="删除某个任务（按 task_guid）；执行前我会先发确认卡片。",
            auth_scopes=TASK_SCOPES,
        )
    )

    # Bitable
    add(
        create_bitable_record(
            description="在多维表格中新增一条记录；执行前我会先发确认卡片。",
            auth_scopes=BITABLE_SCOPES,
        )
    )
    add(
        list_bitable_records(
            description="列出多维表格某个数据表中的记录（可按视图或筛选条件，最多 100 条）。",
            auth_scopes=BITABLE_SCOPES,
        )
    )
    add(
        update_bitable_record(
            description="修改多维表格中的一条记录（按 record_id 与字段）；执行前我会先发确认卡片。",
            auth_scopes=BITABLE_SCOPES,
        )
    )
    add(
        delete_bitable_record(
            description="删除多维表格中的一条记录（按 record_id）；执行前我会先发确认卡片。",
            auth_scopes=BITABLE_SCOPES,
        )
    )

    # Approvals
    add(list_approval_definitions(description="列出你可以发起的审批类型。", auth_scopes=APPROVAL_SCOPES, locale=locale))
    add(get_approval_definition(description="读取某个审批类型的表单结构。", auth_scopes=APPROVAL_SCOPES, locale=locale))
    add(
        create_approval_instance(
            description="发起一个审批；执行前我会先发确认卡片。",
            auth_scopes=APPROVAL_SCOPES,
            locale=locale,
        )
    )
    add(
        get_approval_status(
            description="查询某个审批实例的当前状态与进展（按 instance_code）。",
            auth_scopes=APPROVAL_SCOPES,
        )
    )
    add(
        approve_approval_task(
            description="同意一项待我审批的任务（审批人始终是你本人）；执行前我会先发确认卡片。",
            auth_scopes=APPROVAL_SCOPES,
        )
    )
    add(
        list_my_pending_approvals(
            description="列出待我处理的审批任务（只看你本人的待办）。",
            auth_scopes=APPROVAL_SCOPES,
        )
    )
    add(
        list_my_payment_accounts(
            description="列出你本人的收款账户（脱敏，只给我看尾号和银行）；发起报销等需要收款账户的审批时用它来选账户。",
        )
    )
    add(
        reject_approval_task(
            description="拒绝一项待我审批的任务（审批人始终是你本人）；执行前我会先发确认卡片。",
            auth_scopes=APPROVAL_SCOPES,
        )
    )
    add(
        cancel_approval_instance(
            description="撤回一个你发起的审批实例（按 approval_code 与 instance_code，发起人始终是你本人）；执行前我会先发确认卡片。",
            auth_scopes=APPROVAL_SCOPES,
        )
    )

    # Video conference
    add(
        reserve_meeting(
            description="预约一场视频会议（设置主题与到期时间），返回会议号与入会链接；预约人始终是你本人，执行前我会先发确认卡片。",
            auth_scopes=VC_SCOPES,
            timezone=timezone,
        )
    )
    add(
        update_reservation(
            description="修改一个会议预约的主题或到期时间（按 reserve_id）；执行前我会先发确认卡片。",
            auth_scopes=VC_SCOPES,
            timezone=timezone,
        )
    )
    add(
        cancel_reservation(
            description="取消一个会议预约（按 reserve_id）；执行前我会先发确认卡片。",
            auth_scopes=VC_SCOPES,
        )
    )

    # Documents (create / append / delete)
    add(
        create_document(
            description="在云文档中创建一篇新的空白文档（可指定标题与目标文件夹）；执行前我会先发确认卡片。",
            auth_scopes=DOC_WRITE_SCOPES,
        )
    )
    add(
        append_to_document(
            description="向某篇云文档末尾追加一段文字（按 document_id）；执行前我会先发确认卡片。",
            auth_scopes=DOC_WRITE_SCOPES,
        )
    )
    add(
        list_document_blocks(
            description="列出一篇云文档的各个段落块（含 block_id 与文本），用于定位要改写的段落。",
            auth_scopes=DOC_READ_SCOPES,
        )
    )
    add(
        update_document(
            description="改写某篇云文档中指定段落的文字（按 document_id 与 block_id，先用 list_document_blocks 找到块）；执行前我会先发确认卡片。",
            auth_scopes=DOC_WRITE_SCOPES,
        )
    )
    add(
        delete_document(
            description="将某篇云文档移入回收站（按 document_id）；执行前我会先发确认卡片。",
            auth_scopes=DRIVE_SCOPES,
        )
    )

    # Sheets
    add(
        append_to_sheet(
            description="向电子表格的指定区域追加若干行数据；执行前我会先发确认卡片。",
            auth_scopes=SHEET_SCOPES,
        )
    )
    add(
        update_sheet_range(
            description="覆盖写入电子表格某个区域的数据（按 range 与二维数组）；执行前我会先发确认卡片。",
            auth_scopes=SHEET_SCOPES,
        )
    )
    add(
        delete_sheet_rows(
            description="删除电子表格某个工作表中的若干行（按 sheet_id 与起止行号，1 起含两端）；执行前我会先发确认卡片。",
            auth_scopes=SHEET_SCOPES,
        )
    )
    add(read_sheet_range(description="读取电子表格某个区域的单元格数据。", auth_scopes=SHEET_SCOPES))

    # Mail
    add_scoped(
        list_mail_messages(
            description="列出当前用户邮箱里的邮件 ID；默认读取我的主邮箱，可按文件夹、未读状态或标签筛选。",
            auth_scopes=MAIL_READ_SCOPES,
        )
    )
    add_scoped(
        search_mail_messages(
            description="搜索当前用户邮箱中的邮件；使用飞书邮箱 search 接口，可按关键词和过滤条件搜索。",
            auth_scopes=MAIL_READ_SCOPES,
        )
    )
    add_scoped(
        get_mail_message(
            description="读取当前用户邮箱中某封邮件的详情；先用 list_mail_messages 找到 message_id。",
            auth_scopes=MAIL_READ_SCOPES,
        )
    )
    add_scoped(
        summarize_mail_message(
            description="用 fast 文本模型总结当前用户邮箱中的一封邮件；只返回摘要和元数据，不回传邮件全文。",
            text_summarizer=text_summarizer,
            auth_scopes=MAIL_READ_SCOPES,
            max_body_chars=mail_summary_max_body_chars,
            max_summary_chars=mail_summary_max_chars,
        )
    )
    add_scoped(
        summarize_mail_messages(
            description="用 fast 文本模型总结当前用户最近的若干封邮件；只把摘要和元数据交给主模型，不回传邮件全文。",
            text_summarizer=text_summarizer,
            auth_scopes=MAIL_READ_SCOPES,
            default_max_items=mail_summary_max_messages,
            max_body_chars=mail_summary_max_body_chars,
            max_summary_chars=mail_summary_max_chars,
        )
    )
    add_scoped(
        list_mail_folders(
            description="列出当前用户邮箱的文件夹，用于定位 INBOX、已发送或自定义文件夹。",
            auth_scopes=MAIL_FOLDER_READ_SCOPES,
        )
    )
    add_scoped(
        send_mail_message(
            description="以当前用户身份发送一封邮件；执行前我会先发确认卡片。",
            auth_scopes=MAIL_SEND_SCOPES,
        )
    )

    # People / whiteboard
    add(
        find_user(
            description="在你所在组织内按关键词查找同事，仅返回少量匹配（姓名与 id）。",
            auth_scopes=CONTACT_SCOPES,
        )
    )
    add(
        list_whiteboard_nodes(
            description="读取指定画板内的节点（文本、形状、图片等）。",
            auth_scopes=BOARD_SCOPES,
        )
    )

    # Task comments
    add(
        comment_on_task(
            description="在指定任务下发表一条评论；执行前我会先发确认卡片。",
            auth_scopes=TASK_COMMENT_SCOPES,
        )
    )
    add(
        list_task_comments(
            description="列出某个任务下的评论（按 task_guid）。",
            auth_scopes=TASK_COMMENT_SCOPES,
        )
    )
    add(
        update_task_comment(
            description="修改你发表的某条任务评论（按 comment_id，仅本人可改）；执行前我会先发确认卡片。",
            auth_scopes=TASK_COMMENT_SCOPES,
        )
    )
    add(
        delete_task_comment(
            description="删除某条任务评论（按 comment_id）；执行前我会先发确认卡片。",
            auth_scopes=TASK_COMMENT_SCOPES,
        )
    )

    # Shared files
    add(list_shared_files(description="列出你最近发给我的文件（仅文件名、类型、大小与时间）。"))
    if describe_analyzer is not None:
        add(
            describe_shared_file(
                description="读取并理解你发来的某个文件的内容（图片/PDF/表格等，支持看图）；按 file_id 引用。",
                analyzer=describe_analyzer,
            )
        )
    add(
        upload_shared_file_to_drive(
            description="把你发来的某个文件上传到云空间指定文件夹（按 file_id）；执行前我会先发确认卡片。",
            auth_scopes=DRIVE_SCOPES,
        )
    )

    return registry


class FeishuWorkplaceBundle:
    r"""由 OpenFeishu 原子工具工厂装配出的默认办公场景 bundle。"""

    def register(self, registry: ToolRegistry, context: BundleContext) -> None:
        _build_workplace_tool_registry(
            registry=registry,
            describe_analyzer=context.describe_analyzer,
            text_summarizer=context.text_summarizer,
            locale=context.locale,
            timezone=context.timezone,
            mail_summary_max_messages=context.mail_summary_max_messages,
            mail_summary_max_body_chars=context.mail_summary_max_body_chars,
            mail_summary_max_chars=context.mail_summary_max_chars,
        )


BUNDLES.register(FeishuWorkplaceBundle, name="feishu.workplace", override=True)


__all__ = [
    "APPROVAL_SCOPES",
    "BITABLE_SCOPES",
    "BOARD_SCOPES",
    "CALENDAR_READ_SCOPES",
    "CALENDAR_SCOPES",
    "CONTACT_SCOPES",
    "DOC_READ_SCOPES",
    "DOC_WRITE_SCOPES",
    "DRIVE_SCOPES",
    "FeishuWorkplaceBundle",
    "MAIL_FOLDER_READ_SCOPES",
    "MAIL_READ_SCOPES",
    "MAIL_SEND_SCOPES",
    "ROOM_SCOPES",
    "SHEET_SCOPES",
    "TASK_COMMENT_SCOPES",
    "TASK_SCOPES",
    "VC_SCOPES",
]
