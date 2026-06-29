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

from ..bitable import bitable_record
from ..calendar import DEFAULT_TIMEZONE, calendar_attendees, calendar_event, freebusy_body, unix_seconds
from ..client import FeishuClient
from ..drive.references import DocumentReference, parse_document_reference, raw_document_content

INSTRUCTIONS = """
Use these tools as high-level Feishu capabilities, not as a dump of the whole SDK.
Read private documents only with a user_access_token from the requesting user.
For write operations such as creating calendar events, Bitable records, or approval instances, ask
the user for confirmation before calling the tool.
"""

PDF_DEFAULT_MAX_CHARS = 40_000
PDF_DEFAULT_MAX_PAGES = 4
PDF_DEFAULT_ZOOM = 1.7


def create_server(client: FeishuClient | None = None) -> Any:
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
        """Extract a Feishu document/wiki token and type from a URL or text."""
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
        """Create a Feishu user OAuth URL. Send this to the user when user-scoped access is required."""
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
        Read plain text from a Feishu document with the requesting user's token.

        Never use this with the bot tenant token for user-private documents.
        """
        token = _required(token, "token")
        user_access_token = user_access_token or os.getenv("FEISHU_USER_ACCESS_TOKEN")
        if not user_access_token:
            raise ValueError("user_access_token is required to read document content")
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
        Search Feishu Wiki nodes visible to the requesting user.

        Requires a user_access_token so results are scoped to the user's own permissions.
        """
        query = _required(query, "query")
        user_access_token = user_access_token or os.getenv("FEISHU_USER_ACCESS_TOKEN")
        if not user_access_token:
            raise ValueError("user_access_token is required to search user-visible documents")
        items = await get_client(user_access_token).wiki.search(query, space_id=space_id, max_items=max_items)
        return {"items": _plain(items)}

    @mcp.tool()
    async def feishu_list_drive_files(
        user_access_token: str | None = None,
        folder_token: str | None = None,
        max_items: int | None = 20,
    ) -> dict[str, Any]:
        """
        List Drive files visible to the requesting user.

        Requires a user_access_token so results are scoped to the user's own permissions.
        """
        user_access_token = user_access_token or os.getenv("FEISHU_USER_ACCESS_TOKEN")
        if not user_access_token:
            raise ValueError("user_access_token is required to list user-visible files")
        items = await get_client(user_access_token).drive.files.list(folder_token=folder_token, max_items=max_items)
        return {"items": _plain(items)}

    @mcp.tool()
    def feishu_pdf_to_text(pdf_base64: str, max_chars: int | None = PDF_DEFAULT_MAX_CHARS) -> dict[str, Any]:
        """
        Extract text from a base64-encoded PDF.

        The input may be raw base64 or a data:application/pdf;base64 URL.
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
        Render selected pages from a base64-encoded PDF to PNG data URLs.

        Pages are 1-indexed. Use this when the first rendered pages were not enough to inspect the PDF.
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
        """Download a Feishu message PDF resource and extract its text."""
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
        """Download a Feishu message PDF resource and render selected pages to PNG data URLs."""
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
        """Query Feishu calendar free/busy state for a user or room."""
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
        """Get the primary calendar for the current Feishu identity."""
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
        """List events in a Feishu calendar."""
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
        Get Feishu VC meeting details visible to the requesting user.

        Requires a user_access_token so private meeting data is scoped to the user's own permissions.
        """
        user_access_token = user_access_token or os.getenv("FEISHU_USER_ACCESS_TOKEN")
        if not user_access_token:
            raise ValueError("user_access_token is required to read meeting details")
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
        List Feishu VC meeting instances by meeting number and time window.

        Requires a user_access_token so private meeting data is scoped to the user's own permissions.
        """
        user_access_token = user_access_token or os.getenv("FEISHU_USER_ACCESS_TOKEN")
        if not user_access_token:
            raise ValueError("user_access_token is required to list meeting records")
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
        """List or search Feishu meeting rooms, optionally filtering by capacity and availability."""
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
        """Get Feishu meeting room details by room_id."""
        return {"items": await get_client().meeting_room.batch_get(room_ids)}

    @mcp.tool()
    async def feishu_list_meeting_room_buildings(max_items: int | None = 20) -> dict[str, Any]:
        """List Feishu meeting room buildings."""
        return {"items": await get_client().meeting_room.list_buildings(max_items=max_items)}

    @mcp.tool()
    async def feishu_query_meeting_room_freebusy(
        room_ids: list[str],
        time_min: str,
        time_max: str,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> dict[str, Any]:
        """Query Feishu meeting room free/busy state by room_id."""
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
        """Get a Feishu approval definition, including form metadata for field mapping."""
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
    ) -> dict[str, Any]:
        """
        Create a Feishu calendar event and optionally add attendees/rooms.

        The caller should get explicit user confirmation before invoking this write tool.
        """
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
        instance: dict[str, Any],
        user_access_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a Feishu approval instance, for example an expense claim.

        The caller should get explicit user confirmation before invoking this write tool.
        """
        return await get_client(user_access_token).approval.instances.create(instance)

    @mcp.tool()
    async def feishu_create_bitable_record(
        app_token: str,
        table_id: str,
        fields: dict[str, Any],
        user_access_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Create one Feishu Bitable record.

        The caller should get explicit user confirmation before invoking this write tool.
        """
        record = bitable_record(fields)
        return await get_client(user_access_token).bitable.records.create(
            _required(app_token, "app_token"),
            _required(table_id, "table_id"),
            record.fields,
        )

    return mcp


def main(argv: list[str] | None = None) -> None:
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


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _decode_base64_data(value: str) -> bytes:
    payload = value.strip()
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]
    return base64.b64decode(payload)


def _open_pdf(data: bytes) -> Any:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - optional MCP dependency
        raise RuntimeError("Install open-feishu[mcp] with pymupdf to use PDF tools") from exc
    return fitz.open(stream=data, filetype="pdf")


def _pdf_to_text(data: bytes, *, max_chars: int | None = PDF_DEFAULT_MAX_CHARS) -> dict[str, Any]:
    document = _open_pdf(data)
    try:
        page_count = len(document)
        text = "\n\n".join(page.get_text("text").strip() for page in document).strip()
    finally:
        document.close()
    truncated = False
    if max_chars is not None and max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return {
        "page_count": page_count,
        "text": text,
        "truncated": truncated,
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
        max_pages = max(1, max_pages)
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
    zoom = min(max(zoom, 0.5), 4.0)
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
