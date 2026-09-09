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

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..agent.summarization import TextSummaryRequest
from ..cards.builder import Card
from ..errors import FeishuError
from ..gateway.auth import ServiceAuthError, ServiceCapabilityError, require_service_capability
from ..gateway.config import GatewayConfig
from ..gateway.errors import GatewayRequestError, error_response, feishu_error_response, read_json_object
from ..gateway.notifications import EventMessageStore, InMemoryEventMessageStore, upsert_interactive_card

if TYPE_CHECKING:
    from ..gateway import GatewayContext

TextSummarizer = Callable[[TextSummaryRequest], Awaitable[str | None] | str | None]
NotificationCardBuilder = Callable[[dict[str, Any], str], dict[str, Any]]


@dataclass(frozen=True)
class WorkflowNotificationIntegration:
    r"""Mount a generic service-authenticated notification route on a Feishu gateway."""

    path: str = "/notifications"
    store: EventMessageStore | None = None
    text_summarizer: TextSummarizer | None = None
    card_builder: NotificationCardBuilder | None = None
    max_summary_chars: int = 1200

    def routes(self, context: GatewayContext) -> list[Route]:
        return [
            create_notification_route(
                context.config,
                context.client,
                path=self.path,
                store=self.store,
                text_summarizer=self.text_summarizer,
                card_builder=self.card_builder,
                max_summary_chars=self.max_summary_chars,
            )
        ]


def create_notification_route(
    config: GatewayConfig,
    client: Any,
    *,
    path: str = "/notifications",
    store: EventMessageStore | None = None,
    text_summarizer: TextSummarizer | None = None,
    card_builder: NotificationCardBuilder | None = None,
    max_summary_chars: int = 1200,
) -> Route:
    r"""Create a service-authenticated workflow notification route."""
    return Route(
        path,
        _notification_endpoint(
            config,
            client,
            store=store,
            text_summarizer=text_summarizer,
            card_builder=card_builder,
            max_summary_chars=max_summary_chars,
        ),
        methods=["POST"],
    )


def build_notification_card(payload: dict[str, Any], summary: str) -> dict[str, Any]:
    r"""Render a normalized workflow/status notification as a Feishu card."""
    title = _text(payload.get("title") or payload.get("name") or "流程通知")
    status = _text(payload.get("status"))
    source = _text(payload.get("source") or payload.get("system"))
    template = _template(status)
    title_parts = [part for part in (status.upper() if status else "", title) if part]
    card = Card().header(" - ".join(title_parts), template=template).markdown(summary or title)

    metadata = []
    event_id = notification_event_id(payload)
    if event_id:
        metadata.append(f"**ID**: `{event_id}`")
    if source:
        metadata.append(f"**Source**: {source}")
    if status:
        metadata.append(f"**Status**: {status}")
    if metadata:
        card.divider().markdown("\n".join(metadata))

    links = _links(payload.get("links"))
    url = _text(payload.get("url") or payload.get("source_url"))
    if url:
        links = [*links, {"title": "Open source", "url": url}]
    if links:
        card.markdown("\n".join(f"[{item['title']}]({item['url']})" for item in links[:5]))
    return card.to_dict()


def notification_event_id(payload: Mapping[str, Any]) -> str:
    r"""Return the stable notification ID used for card upserts."""
    for key in ("event_id", "idempotency_key", "notification_id", "id"):
        value = _text(payload.get(key))
        if value:
            return value
    source = _text(payload.get("source") or payload.get("system"))
    title = _text(payload.get("title") or payload.get("name"))
    status = _text(payload.get("status"))
    return "/".join(part for part in (source, title, status) if part)


def _notification_endpoint(
    config: GatewayConfig,
    client: Any,
    *,
    store: EventMessageStore | None,
    text_summarizer: TextSummarizer | None,
    card_builder: NotificationCardBuilder | None,
    max_summary_chars: int,
) -> Callable[[Request], Awaitable[Response]]:
    event_store = store or InMemoryEventMessageStore()
    build_card = card_builder or build_notification_card

    async def endpoint(request: Request) -> Response:
        try:
            service = require_service_capability(request, config.service_keys, config.service_capabilities)
            payload = await read_json_object(request)
            event_id = notification_event_id(payload)
            if not event_id:
                raise GatewayRequestError("notification payload requires event_id or idempotency_key", 400)
            target_id, target_type = _target(payload)
            summary = await notification_summary(payload, text_summarizer=text_summarizer, max_chars=max_summary_chars)
            card = build_card(payload, summary)
            delivery = await upsert_interactive_card(
                client,
                event_id,
                card,
                target_id,
                receive_id_type=target_type,
                store=event_store,
                uuid_prefix="notify-",
            )
            return JSONResponse(
                {
                    "action": delivery.action,
                    "event_id": delivery.event_id,
                    "message_id": delivery.message_id,
                    "service": service,
                }
            )
        except ServiceAuthError:
            return error_response("unauthorized", status_code=401)
        except ServiceCapabilityError:
            return error_response("forbidden", status_code=403)
        except GatewayRequestError as exc:
            return error_response(exc.message, status_code=exc.status_code)
        except FeishuError as exc:
            return feishu_error_response(exc)

    return endpoint


async def notification_summary(
    payload: Mapping[str, Any],
    *,
    text_summarizer: TextSummarizer | None,
    max_chars: int = 1200,
) -> str:
    r"""Summarize a notification payload with a fast text summarizer when configured."""
    explicit = _text(payload.get("summary") or payload.get("message"))
    text = _text(payload.get("summary_input") or payload.get("text") or payload.get("description"))
    if not text:
        text = _compact_json(payload.get("details") or payload)
    if text_summarizer is None:
        return _truncate(explicit or text, max_chars=max_chars)
    instruction = _text(payload.get("summary_instruction")) or (
        "Summarize this workflow/status event for the target recipient. "
        "Keep it concise, include the current state, important risks, and any next action."
    )
    request = TextSummaryRequest(
        kind=_text(payload.get("kind") or payload.get("type") or "workflow_notification"),
        instruction=instruction,
        text=f"{explicit}\n\n{text}".strip(),
        max_chars=max_chars,
        language=_text(payload.get("language")) or "zh-CN",
    )
    result = text_summarizer(request)
    if inspect.isawaitable(result):
        result = await result
    return _truncate(_text(result) or explicit or text, max_chars=max_chars)


def _target(
    payload: Mapping[str, Any],
) -> tuple[str, str]:
    target = payload.get("target")
    target_map = target if isinstance(target, Mapping) else {}
    receive_id = _text(target_map.get("receive_id") or payload.get("receive_id"))
    if not receive_id:
        raise GatewayRequestError("notification payload requires target.receive_id", 400)
    receive_id_type = _text(target_map.get("receive_id_type") or payload.get("receive_id_type"))
    return receive_id, receive_id_type or "chat_id"


def _template(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"success", "succeeded", "ok", "done", "completed", "resolved"}:
        return "green"
    if normalized in {"failed", "failure", "error", "critical", "p0"}:
        return "red"
    if normalized in {"running", "processing", "pending", "started"}:
        return "blue"
    if normalized in {"warning", "warn", "attention", "blocked"}:
        return "orange"
    return "blue"


def _links(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    links: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        url = _text(item.get("url") or item.get("href"))
        if not url:
            continue
        title = _text(item.get("title") or item.get("label") or "Open")
        links.append({"title": title, "url": url})
    return links


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _truncate(value: str, *, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ("" if value is None else str(value).strip())
