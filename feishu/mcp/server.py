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

import argparse
import base64
import os
from typing import Any

from chanfig import NestedDict

from ..approval import approval_instance
from ..bitable import bitable_record
from ..calendar import DEFAULT_TIMEZONE, calendar_attendees, calendar_event, freebusy_body, unix_seconds
from ..client import FeishuClient
from ..drive.references import DocumentReference, parse_document_reference, raw_document_content

INSTRUCTIONS = """
Use these tools as high-level Feishu capabilities, not as a dump of the whole SDK.
Read private documents only with an explicit user_access_token from the requesting user.
For write operations such as creating calendar events, Bitable records, or approval instances, ask the user
for confirmation before calling the tool.
"""

PDF_DEFAULT_MAX_CHARS = 40_000
PDF_DEFAULT_MAX_PAGES = 4
PDF_DEFAULT_ZOOM = 1.7
PDF_MAX_BYTES = 20 * 1024 * 1024
PDF_MAX_RENDER_PAGES = 8
PDF_MAX_ZOOM = 3.0


def create_server(client: FeishuClient | None = None) -> Any:
    r"""
    创建 OpenFeishu MCP 服务器。

    Args:
        client: 可选基础 [feishu.client.FeishuClient][]。未传入时会从环境变量构造：
            `FEISHU_APP_ID` / `APP_ID`、`FEISHU_APP_SECRET` / `APP_SECRET`，
            以及 `FEISHU_REGION` / `REGION`。

    Returns:
        已注册飞书工具的 `FastMCP` 实例。

    Raises:
        RuntimeError: 未安装 `open-feishu[mcp]` 额外依赖时抛出。
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError("Install open-feishu[mcp] to use the MCP server") from exc

    mcp = FastMCP("open-feishu", instructions=INSTRUCTIONS.strip())
    base_client = client

    def get_client(user_access_token: str | None = None) -> FeishuClient:
        nonlocal base_client
        if base_client is None:
            base_client = FeishuClient(
                _env("FEISHU_APP_ID", "APP_ID"),
                _env("FEISHU_APP_SECRET", "APP_SECRET"),
                region=_env("FEISHU_REGION", "REGION", default="feishu") or "feishu",
            )
        if user_access_token:
            return base_client.as_user(user_access_token)
        return base_client

    @mcp.tool()
    def feishu_parse_document_reference(text: str) -> dict[str, str | None]:
        """从 URL 或文本中提取飞书文档 / 知识库 token 与类型。"""
        reference = parse_document_reference(text)
        if reference is None:
            return {"token": None, "doc_type": None}
        return {"token": reference.token, "doc_type": reference.doc_type}

    @mcp.tool()
    async def feishu_authorize_url(
        redirect_uri: str,
        scope: str | None = None,
        state: str | None = None,
        prompt: str | None = None,
    ) -> dict[str, str]:
        """创建飞书用户 OAuth 授权 URL；需要用户态访问时把该链接发给用户。"""
        scopes = scope.split() if scope else None
        return {
            "url": get_client().oauth.authorize_url(
                redirect_uri,
                scope=scopes,
                state=state,
                prompt=prompt,
            )
        }

    @mcp.tool()
    async def feishu_read_document_raw_content(
        token: str,
        doc_type: str | None = None,
        user_access_token: str | None = None,
        lang: int | None = 1,
    ) -> dict[str, str | None]:
        """
        使用请求用户的 token 读取飞书文档纯文本。

        用户私有文档必须使用 `user_access_token`，不要用机器人租户 token 读取。
        """
        token = _required(token, "token")
        user_access_token = _required_user_access_token(user_access_token, "read document content")
        reference = DocumentReference(token=token, doc_type=doc_type)
        content = await raw_document_content(get_client(user_access_token), reference, lang=lang)
        return {"token": token, "doc_type": doc_type, "content": content}

    @mcp.tool()
    async def feishu_search_wiki(
        query: str,
        user_access_token: str | None = None,
        space_id: str | None = None,
        max_items: int | None = 10,
    ) -> dict[str, Any]:
        """
        搜索请求用户可见的飞书知识库节点。

        需要 `user_access_token`，确保结果受用户自身权限约束。
        """
        query = _required(query, "query")
        user_access_token = _required_user_access_token(user_access_token, "search user-visible documents")
        items = await get_client(user_access_token).wiki.search(query, space_id=space_id, max_items=max_items)
        return {"items": _plain(items)}

    @mcp.tool()
    async def feishu_list_drive_files(
        user_access_token: str | None = None,
        folder_token: str | None = None,
        max_items: int | None = 20,
    ) -> dict[str, Any]:
        """
        列出请求用户可见的云空间文件。

        需要 `user_access_token`，确保结果受用户自身权限约束。
        """
        user_access_token = _required_user_access_token(user_access_token, "list user-visible files")
        items = await get_client(user_access_token).drive.files.list(folder_token=folder_token, max_items=max_items)
        return {"items": _plain(items)}

    @mcp.tool()
    def feishu_pdf_to_text(pdf_base64: str, max_chars: int | None = PDF_DEFAULT_MAX_CHARS) -> dict[str, Any]:
        """
        从 base64 编码的 PDF 中提取文本。

        输入可以是裸 base64，也可以是 `data:application/pdf;base64` URL。
        """
        return _pdf_to_text(_decode_base64_data(pdf_base64), max_chars=max_chars)

    @mcp.tool()
    def feishu_pdf_to_images(
        pdf_base64: str,
        start_page: int = 1,
        max_pages: int = PDF_DEFAULT_MAX_PAGES,
        zoom: float = PDF_DEFAULT_ZOOM,
    ) -> dict[str, Any]:
        """
        将 base64 编码 PDF 的指定页面渲染为 PNG data URL。

        页码从 1 开始；当首批页面不足以检查 PDF 时使用。
        """
        return _pdf_to_images(
            _decode_base64_data(pdf_base64),
            start_page=start_page,
            max_pages=max_pages,
            zoom=zoom,
        )

    @mcp.tool()
    async def feishu_message_pdf_to_text(
        message_id: str,
        file_key: str,
        user_access_token: str | None = None,
        resource_type: str = "file",
        max_chars: int | None = PDF_DEFAULT_MAX_CHARS,
    ) -> dict[str, Any]:
        """下载飞书消息中的 PDF 资源并提取文本。"""
        user_access_token = _required_user_access_token(user_access_token, "read message resources")
        data = await get_client(user_access_token).im.get_resource(
            _required(message_id, "message_id"),
            _required(file_key, "file_key"),
            resource_type=resource_type,
        )
        return _pdf_to_text(data, max_chars=max_chars)

    @mcp.tool()
    async def feishu_message_pdf_to_images(
        message_id: str,
        file_key: str,
        user_access_token: str | None = None,
        resource_type: str = "file",
        start_page: int = 1,
        max_pages: int = PDF_DEFAULT_MAX_PAGES,
        zoom: float = PDF_DEFAULT_ZOOM,
    ) -> dict[str, Any]:
        """下载飞书消息中的 PDF 资源并把指定页面渲染为 PNG data URL。"""
        user_access_token = _required_user_access_token(user_access_token, "read message resources")
        data = await get_client(user_access_token).im.get_resource(
            _required(message_id, "message_id"),
            _required(file_key, "file_key"),
            resource_type=resource_type,
        )
        return _pdf_to_images(data, start_page=start_page, max_pages=max_pages, zoom=zoom)

    @mcp.tool()
    async def feishu_query_freebusy(
        time_min: str,
        time_max: str,
        user_id: str | None = None,
        room_id: str | None = None,
        user_access_token: str | None = None,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> dict[str, Any]:
        """查询用户或会议室的飞书日历忙闲状态。"""
        user_access_token = _required_user_access_token(user_access_token, "query calendar free/busy")
        body = freebusy_body(
            time_min=time_min,
            time_max=time_max,
            user_id=user_id,
            room_id=room_id,
            timezone=timezone,
        )
        return await get_client(user_access_token).calendar.freebusy.query(body)

    @mcp.tool()
    async def feishu_get_primary_calendar(user_access_token: str | None = None) -> dict[str, Any]:
        """获取当前飞书身份的主日历。"""
        user_access_token = _required_user_access_token(user_access_token, "get a primary calendar")
        return await get_client(user_access_token).calendar.calendars.primary()

    @mcp.tool()
    async def feishu_list_calendar_events(
        calendar_id: str,
        start_time: str | None = None,
        end_time: str | None = None,
        user_access_token: str | None = None,
        max_items: int | None = 20,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> list[NestedDict]:
        """列出指定飞书日历中的日程。"""
        user_access_token = _required_user_access_token(user_access_token, "list calendar events")
        return await get_client(user_access_token).calendar.events.list(
            _required(calendar_id, "calendar_id"),
            start_time=str(unix_seconds(start_time, timezone=timezone)) if start_time else None,
            end_time=str(unix_seconds(end_time, timezone=timezone)) if end_time else None,
            max_items=max_items,
        )

    @mcp.tool()
    async def feishu_get_vc_meeting(
        meeting_id: str,
        user_access_token: str | None = None,
        with_participants: bool | None = True,
        with_meeting_ability: bool | None = None,
        user_id_type: str | None = None,
    ) -> dict[str, Any]:
        """
        获取请求用户可见的飞书视频会议详情。

        需要 `user_access_token`，确保私有会议数据受用户自身权限约束。
        """
        user_access_token = _required_user_access_token(user_access_token, "read meeting details")
        return await get_client(user_access_token).vc.meetings.get(
            _required(meeting_id, "meeting_id"),
            with_participants=with_participants,
            with_meeting_ability=with_meeting_ability,
            user_id_type=user_id_type,
        )

    @mcp.tool()
    async def feishu_list_vc_meetings_by_no(
        meeting_no: str,
        start_time: str,
        end_time: str,
        user_access_token: str | None = None,
        max_items: int | None = 10,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> dict[str, Any]:
        """
        按会议号和时间窗口列出飞书视频会议实例。

        需要 `user_access_token`，确保私有会议数据受用户自身权限约束。
        """
        user_access_token = _required_user_access_token(user_access_token, "list meeting records")
        items = await get_client(user_access_token).vc.meetings.list_by_no(
            _required(meeting_no, "meeting_no"),
            str(unix_seconds(start_time, timezone=timezone)),
            str(unix_seconds(end_time, timezone=timezone)),
            max_items=max_items,
        )
        return {"items": _plain(items)}

    @mcp.tool()
    async def feishu_list_meeting_rooms(
        building_id: str | None = None,
        query: str | None = None,
        min_capacity: int | None = None,
        available_start_time: str | None = None,
        available_end_time: str | None = None,
        max_items: int | None = 20,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> dict[str, Any]:
        """列出或搜索飞书会议室，可按容量与可用性过滤。"""
        if building_id:
            rooms = await get_client().meeting_room.list(
                building_id=building_id,
                max_items=max_items,
            )
        else:
            rooms = []
            for building in await get_client().meeting_room.list_buildings(max_items=10):
                current_building_id = building.get("building_id") or building.get("id")
                if not current_building_id:
                    continue
                current = await get_client().meeting_room.list(
                    building_id=current_building_id,
                    max_items=max_items,
                )
                rooms.extend(current)
                if max_items is not None and len(rooms) >= max_items:
                    rooms = rooms[:max_items]
                    break
        rooms = _filter_rooms(rooms, query=query, min_capacity=min_capacity)
        if available_start_time and available_end_time and rooms:
            freebusy = await get_client().meeting_room.freebusy(
                [room["room_id"] for room in rooms if room.get("room_id")],
                time_min=available_start_time,
                time_max=available_end_time,
                timezone=timezone,
            )
            rooms = _available_rooms(rooms, freebusy)
        return {"items": rooms}

    @mcp.tool()
    async def feishu_get_meeting_rooms(room_ids: list[str]) -> dict[str, Any]:
        """按 `room_id` 获取飞书会议室详情。"""
        return {"items": await get_client().meeting_room.batch_get(room_ids)}

    @mcp.tool()
    async def feishu_list_meeting_room_buildings(max_items: int | None = 20) -> dict[str, Any]:
        """列出飞书会议室建筑。"""
        return {"items": await get_client().meeting_room.list_buildings(max_items=max_items)}

    @mcp.tool()
    async def feishu_query_meeting_room_freebusy(
        room_ids: list[str],
        time_min: str,
        time_max: str,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> dict[str, Any]:
        """按 `room_id` 查询飞书会议室忙闲状态。"""
        return await get_client().meeting_room.freebusy(
            room_ids,
            time_min=time_min,
            time_max=time_max,
            timezone=timezone,
        )

    @mcp.tool()
    async def feishu_get_approval_definition(
        approval_code: str,
        locale: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """获取飞书审批定义，包括用于字段映射的表单元数据。"""
        return await get_client().approval.definitions.get(
            _required(approval_code, "approval_code"),
            locale=locale,
            user_id=user_id,
        )

    @mcp.tool()
    async def feishu_create_calendar_event(
        calendar_id: str,
        event: dict[str, Any] | None = None,
        summary: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        attendees: list[dict[str, Any]] | None = None,
        user_access_token: str | None = None,
        idempotency_key: str | None = None,
        timezone: str = DEFAULT_TIMEZONE,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """
        创建飞书日程，并可选添加参与人或会议室。

        调用该写工具前，调用方应先取得用户明确确认。
        """
        _require_confirmed(confirmed)
        calendar_id = _required(calendar_id, "calendar_id")
        event_payload = event
        if event_payload is None:
            event_payload = calendar_event(
                summary=_required(summary, "summary"),
                start_time=_required(start_time, "start_time"),
                end_time=_required(end_time, "end_time"),
                timezone=timezone,
            )
        result = await get_client(user_access_token).calendar.events.create(
            calendar_id,
            event_payload,
            idempotency_key=idempotency_key,
        )
        event_data = result.get("event") or {}
        event_id = event_data.get("event_id")
        if attendees and event_id:
            attendee_result = await get_client(user_access_token).calendar.attendees.add(
                calendar_id,
                str(event_id),
                calendar_attendees(attendees),
            )
            result["attendees_result"] = attendee_result
        return result

    @mcp.tool()
    async def feishu_create_approval_instance(
        approval_code: str,
        form: dict[str, Any] | list[dict[str, Any]] | str,
        department_id: str | None = None,
        user_access_token: str | None = None,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """
        为请求用户创建飞书审批实例。

        调用该写工具前，调用方应先取得用户明确确认。申请人从 `user_access_token` 解析；
        不接受调用方提供的 `user_id` / `open_id` 字段。
        """
        _require_confirmed(confirmed)
        user_access_token = _required_user_access_token(user_access_token, "create approval instances")
        user = await get_client().oauth.user_info(user_access_token)
        user_id = user.get("user_id")
        open_id = user.get("open_id")
        if not user_id and not open_id:
            raise ValueError("user_access_token did not resolve to an approval applicant id")
        payload = approval_instance(
            _required(approval_code, "approval_code"),
            form=form,
            user_id=user_id,
            open_id=open_id,
            department_id=department_id,
        )
        return await get_client().approval.instances.create(payload)

    @mcp.tool()
    async def feishu_create_bitable_record(
        app_token: str,
        table_id: str,
        fields: dict[str, Any],
        user_access_token: str | None = None,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """
        创建一条飞书多维表格记录。

        调用该写工具前，调用方应先取得用户明确确认。
        """
        _require_confirmed(confirmed)
        record = bitable_record(fields)
        return await get_client(user_access_token).bitable.records.create(
            _required(app_token, "app_token"),
            _required(table_id, "table_id"),
            record.fields,
        )

    return mcp


def main(argv: list[str] | None = None) -> None:
    r"""
    运行 `feishu-mcp` 命令行入口。

    Args:
        argv: 可选命令行参数列表；为 `None` 时从当前进程参数读取。第一个位置参数为 MCP
            传输方式，可取 `stdio`、`sse` 或 `streamable-http`，默认 `stdio`。
    """
    parser = argparse.ArgumentParser(description="Run the OpenFeishu MCP server")
    parser.add_argument(
        "transport",
        nargs="?",
        choices=("stdio", "sse", "streamable-http"),
        default="stdio",
    )
    args = parser.parse_args(argv)
    create_server().run(transport=args.transport)


def _env(primary: str, fallback: str | None = None, *, default: str | None = None) -> str | None:
    value = os.getenv(primary)
    if value is None and fallback is not None:
        value = os.getenv(fallback)
    return value if value is not None else default


def _required(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _required_user_access_token(value: str | None, purpose: str) -> str:
    if not value:
        raise ValueError(f"user_access_token is required to {purpose}")
    return value


def _require_confirmed(confirmed: bool) -> None:
    if confirmed is not True:
        raise ValueError("explicit confirmation is required; call this write tool with confirmed=True")


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _decode_base64_data(value: str, *, max_bytes: int = PDF_MAX_BYTES) -> bytes:
    payload = value.strip()
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]
    data = base64.b64decode(payload, validate=True)
    if len(data) > max_bytes:
        raise ValueError(f"base64 payload is too large: {len(data)} bytes > {max_bytes} bytes")
    return data


def _open_pdf(data: bytes) -> Any:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - optional MCP dependency
        raise RuntimeError("Install open-feishu[mcp] with pymupdf to use PDF tools") from exc
    return fitz.open(stream=data, filetype="pdf")


def _pdf_to_text(
    data: bytes,
    *,
    max_chars: int | None = PDF_DEFAULT_MAX_CHARS,
    max_pages: int = PDF_MAX_RENDER_PAGES,
) -> dict[str, Any]:
    document = _open_pdf(data)
    try:
        page_count = len(document)
        page_limit = min(page_count, max(1, min(max_pages, PDF_MAX_RENDER_PAGES)))
        text = "\n\n".join(document[index].get_text("text").strip() for index in range(page_limit)).strip()
    finally:
        document.close()
    truncated = False
    if max_chars is not None and max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return {
        "page_count": page_count,
        "read_pages": page_limit,
        "text": text,
        "truncated": truncated or page_limit < page_count,
        "max_chars": max_chars,
    }


def _pdf_to_images(
    data: bytes,
    *,
    start_page: int = 1,
    max_pages: int = PDF_DEFAULT_MAX_PAGES,
    zoom: float = PDF_DEFAULT_ZOOM,
) -> dict[str, Any]:
    document = _open_pdf(data)
    try:
        page_count = len(document)
        start_page = max(1, start_page)
        max_pages = max(1, min(max_pages, PDF_MAX_RENDER_PAGES))
        start_index = min(start_page - 1, page_count)
        end_index = min(page_count, start_index + max_pages)
        matrix = _pdf_matrix(zoom)
        images = []
        for page_index in range(start_index, end_index):
            pixmap = document[page_index].get_pixmap(matrix=matrix, alpha=False)
            image = pixmap.tobytes("png")
            images.append(
                {
                    "page": page_index + 1,
                    "media_type": "image/png",
                    "data_url": f"data:image/png;base64,{base64.b64encode(image).decode('ascii')}",
                    "bytes": len(image),
                }
            )
    finally:
        document.close()
    return {
        "page_count": page_count,
        "start_page": start_page,
        "rendered_pages": [item["page"] for item in images],
        "images": images,
    }


def _pdf_matrix(zoom: float) -> Any:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - optional MCP dependency
        raise RuntimeError("Install open-feishu[mcp] with pymupdf to use PDF tools") from exc
    zoom = min(max(zoom, 0.5), PDF_MAX_ZOOM)
    return fitz.Matrix(zoom, zoom)


def _filter_rooms(
    rooms: list[NestedDict],
    *,
    query: str | None = None,
    min_capacity: int | None = None,
) -> list[NestedDict]:
    if query:
        needle = query.lower()
        rooms = [
            room
            for room in rooms
            if needle
            in " ".join(
                str(room.get(key) or "") for key in ("name", "building_name", "floor_name", "display_id")
            ).lower()
        ]
    if min_capacity is not None:
        rooms = [room for room in rooms if int(room.get("capacity") or 0) >= min_capacity]
    return rooms


def _available_rooms(rooms: list[NestedDict], freebusy: dict[str, Any]) -> list[NestedDict]:
    busy = freebusy.get("free_busy") or {}
    return [room for room in rooms if not busy.get(room.get("room_id"))]


if __name__ == "__main__":  # pragma: no cover
    main()
