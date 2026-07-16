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

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..cards.factories import alert_card
from ..errors import FeishuError
from ..gateway.auth import ServiceAuthError, ServiceCapabilityError, require_service_capability
from ..gateway.config import GatewayConfig
from ..gateway.errors import GatewayRequestError, error_response, feishu_error_response, read_json_object
from ..gateway.notifications import (
    EventMessageStore,
    InMemoryEventMessageStore,
    JsonFileEventMessageStore,
    upsert_interactive_card,
)

if TYPE_CHECKING:
    from ..gateway import GatewayContext

AlertmanagerMessageStore = EventMessageStore
InMemoryAlertmanagerStore = InMemoryEventMessageStore
JsonFileAlertmanagerStore = JsonFileEventMessageStore


@dataclass(frozen=True)
class AlertmanagerIntegration:
    r"""Mount Alertmanager webhooks on a Feishu gateway."""

    receive_id: str
    receive_id_type: str = "chat_id"
    path: str = "/alerts/alertmanager"
    store: AlertmanagerMessageStore | None = None

    def routes(self, context: GatewayContext) -> list[Route]:
        return [
            create_alertmanager_route(
                context.config,
                context.client,
                self.receive_id,
                receive_id_type=self.receive_id_type,
                path=self.path,
                store=self.store,
            )
        ]


def create_alertmanager_route(
    config: GatewayConfig,
    client: Any,
    receive_id: str,
    *,
    receive_id_type: str = "chat_id",
    path: str = "/alerts/alertmanager",
    store: AlertmanagerMessageStore | None = None,
) -> Route:
    r"""Create a service-authenticated Alertmanager webhook route.

    The route converts the standard Alertmanager webhook payload into a Feishu
    interactive card. It uses the Alertmanager ``groupKey`` or alert
    ``fingerprint`` as a stable event ID, so repeated notifications update the
    original Feishu card instead of creating a new post.
    """
    return Route(
        path,
        _alertmanager_endpoint(config, client, receive_id, receive_id_type=receive_id_type, store=store),
        methods=["POST"],
    )


def build_alertmanager_card(payload: dict[str, Any]) -> dict[str, Any]:
    r"""Render an Alertmanager webhook payload as a Feishu alert card."""
    status = _status(payload)
    priority = _priority(payload)
    labels = _dict(payload.get("commonLabels"))
    annotations = _dict(payload.get("commonAnnotations"))
    alerts = [alert for alert in payload.get("alerts", []) if isinstance(alert, dict)]
    title = _alert_title(payload)
    cluster = _text(labels.get("cluster"))
    severity = _text(labels.get("severity"))
    summary = _text(annotations.get("summary") or annotations.get("description"))
    display_id = alertmanager_display_id(payload)

    title_parts = [status.upper()]
    if priority:
        title_parts.append(priority)
    title_parts.append(title)
    if cluster:
        title_parts.append(cluster)

    lines = [
        f"**ID**: `{display_id}`",
        f"**Status**: {status}",
    ]
    if priority:
        lines.append(f"**Priority**: {priority}")
    if cluster:
        lines.append(f"**Cluster**: {cluster}")
    if severity:
        lines.append(f"**Severity**: {severity}")
    if summary and summary != title:
        lines.append(f"**Summary**: {summary}")
    if alerts:
        lines.append("")
        lines.append("**Instances**:")
        for alert in alerts[:8]:
            lines.append(f"- {_alert_line(alert)}")
    external_url = _text(payload.get("externalURL"))
    if external_url:
        lines.append("")
        lines.append(f"[Open Alertmanager]({external_url})")

    return alert_card(
        "\n".join(lines),
        title=" - ".join(title_parts),
        template=_template(status, priority),
    )


def alertmanager_event_id(payload: Mapping[str, Any]) -> str:
    r"""Return the stable event ID for an Alertmanager webhook payload."""
    group_key = _text(payload.get("groupKey"))
    if group_key:
        return group_key

    alerts = [alert for alert in payload.get("alerts", []) if isinstance(alert, Mapping)]
    fingerprints = sorted(_text(alert.get("fingerprint")) for alert in alerts if _text(alert.get("fingerprint")))
    if len(fingerprints) == 1:
        return fingerprints[0]
    if fingerprints:
        return "fingerprints:" + ",".join(fingerprints)

    stable = {
        "receiver": _text(payload.get("receiver")),
        "commonLabels": _dict(payload.get("commonLabels")),
        "commonAnnotations": _dict(payload.get("commonAnnotations")),
        "alerts": [_dict(alert.get("labels")) for alert in alerts],
    }
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"sha256:{digest}"


def alertmanager_display_id(payload: Mapping[str, Any]) -> str:
    r"""Return a user-readable Alertmanager identifier for cards."""
    labels = _dict(payload.get("commonLabels"))
    alerts = [alert for alert in payload.get("alerts", []) if isinstance(alert, Mapping)]
    if len(alerts) == 1:
        labels = {**_dict(alerts[0].get("labels")), **labels}

    parts = [
        _text(labels.get("alertname")),
        _text(labels.get("cluster")),
        _text(labels.get("node")),
        _text(labels.get("device")),
        _text(labels.get("service")),
        _text(labels.get("job")),
        _text(labels.get("instance")),
    ]
    readable = [part for part in parts if part]
    if readable:
        return "/".join(readable)

    fingerprints = sorted(_text(alert.get("fingerprint")) for alert in alerts if _text(alert.get("fingerprint")))
    if len(fingerprints) == 1:
        return fingerprints[0]
    if fingerprints:
        return "fingerprints:" + ",".join(fingerprints)
    return alertmanager_event_id(payload)


def _alertmanager_endpoint(
    config: GatewayConfig,
    client: Any,
    receive_id: str,
    *,
    receive_id_type: str,
    store: AlertmanagerMessageStore | None,
) -> Callable[[Request], Awaitable[Response]]:
    event_store = store or InMemoryAlertmanagerStore()

    async def endpoint(request: Request) -> Response:
        try:
            require_service_capability(
                request,
                config.service_keys,
                config.service_capabilities,
            )
            payload = await read_json_object(request)
            event_id = alertmanager_event_id(payload)
            card = build_alertmanager_card(payload)
            delivery = await upsert_interactive_card(
                client,
                event_id,
                card,
                receive_id,
                receive_id_type=receive_id_type,
                store=event_store,
                uuid_prefix="am-",
            )
            return JSONResponse(
                {
                    "action": delivery.action,
                    "event_id": delivery.event_id,
                    "message_id": delivery.message_id,
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


def _alert_title(payload: Mapping[str, Any]) -> str:
    labels = _dict(payload.get("commonLabels"))
    annotations = _dict(payload.get("commonAnnotations"))
    return (
        _text(annotations.get("title"))
        or _text(annotations.get("summary"))
        or _text(labels.get("alertname"))
        or "Alertmanager alert"
    )


def _alert_line(alert: dict[str, Any]) -> str:
    labels = _dict(alert.get("labels"))
    annotations = _dict(alert.get("annotations"))
    node = _text(labels.get("node") or labels.get("instance") or labels.get("service") or labels.get("job"))
    fingerprint = _text(alert.get("fingerprint"))
    description = _text(annotations.get("description") or annotations.get("summary"))
    status = _text(alert.get("status"))
    url = _text(alert.get("generatorURL"))
    parts = []
    if node:
        parts.append(f"`{node}`")
    if status:
        parts.append(status)
    if description:
        parts.append(description)
    if fingerprint:
        parts.append(f"`{fingerprint}`")
    line = " - ".join(parts) if parts else "alert"
    if url:
        line = f"{line} ([source]({url}))"
    return line


def _priority(payload: Mapping[str, Any]) -> str:
    labels = _dict(payload.get("commonLabels"))
    annotations = _dict(payload.get("commonAnnotations"))
    raw = _text(labels.get("priority") or labels.get("severity") or annotations.get("priority")).lower()
    if raw in {"p0", "critical", "crit", "page", "fatal"}:
        return "P0"
    if raw in {"p1", "high", "error"}:
        return "P1"
    if raw in {"p2", "medium", "warning", "warn"}:
        return "P2"
    if raw in {"p3", "low", "info", "notice"}:
        return "P3"
    return ""


def _status(payload: Mapping[str, Any]) -> str:
    labels = _dict(payload.get("commonLabels"))
    annotations = _dict(payload.get("commonAnnotations"))
    state = _text(
        labels.get("state")
        or labels.get("workflow_status")
        or labels.get("lifecycle")
        or annotations.get("state")
        or annotations.get("workflow_status")
    ).lower()
    if state in {"ack", "acked", "acknowledged", "processing", "in_progress", "silenced"}:
        return "processing"
    if state in {"resolved", "closed", "done"}:
        return "resolved"
    return _text(payload.get("status")) or "unknown"


def _template(status: str, priority: str) -> str:
    normalized = status.lower()
    if normalized == "resolved":
        return "green"
    if normalized == "processing":
        return "blue"
    return {"P0": "red", "P1": "orange", "P2": "yellow", "P3": "grey"}.get(priority, "yellow")


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""
