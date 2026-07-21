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

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

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

_AlertRevision = tuple[float, int]
_AlertRevisions = dict[str, _AlertRevision]
AlertmanagerCardBuilder = Callable[[dict[str, Any]], dict[str, Any]]


class AlertmanagerMessageStore(EventMessageStore, Protocol):
    r"""Store Feishu message IDs and per-alert lifecycle revisions."""

    def get_alert_revisions(self, event_id: str) -> _AlertRevisions:
        r"""Return lifecycle revisions keyed by Alertmanager fingerprint."""
        ...

    def set_alert_revisions(
        self,
        event_id: str,
        revisions: _AlertRevisions,
    ) -> None:
        r"""Persist lifecycle revisions for an Alertmanager notification group."""
        ...


class InMemoryAlertmanagerStore(InMemoryEventMessageStore):
    r"""Process-local Alertmanager message and alert lifecycle state."""

    def __init__(self) -> None:
        super().__init__()
        self._alert_revisions: dict[str, _AlertRevisions] = {}

    def get_alert_revisions(self, event_id: str) -> _AlertRevisions:
        return dict(self._alert_revisions.get(event_id, {}))

    def set_alert_revisions(
        self,
        event_id: str,
        revisions: _AlertRevisions,
    ) -> None:
        self._alert_revisions[event_id] = dict(revisions)


class JsonFileAlertmanagerStore(JsonFileEventMessageStore):
    r"""Single-process JSON-file Alertmanager message and lifecycle state."""

    def __init__(self, path: str | Path) -> None:
        super().__init__(path)
        self._alerts_path = Path(f"{self.path}.alerts")

    def get_alert_revisions(self, event_id: str) -> _AlertRevisions:
        with self._lock:
            return dict(self._read_alert_revisions().get(event_id, {}))

    def set_alert_revisions(
        self,
        event_id: str,
        revisions: _AlertRevisions,
    ) -> None:
        with self._lock:
            state = self._read_alert_revisions()
            state[event_id] = dict(revisions)
            self._alerts_path.parent.mkdir(parents=True, exist_ok=True)
            serializable = {
                group: {fingerprint: [revision[0], revision[1]] for fingerprint, revision in alerts.items()}
                for group, alerts in state.items()
            }
            self._alerts_path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    def _read_alert_revisions(self) -> dict[str, _AlertRevisions]:
        if not self._alerts_path.exists():
            return {}
        data = json.loads(self._alerts_path.read_text())
        if not isinstance(data, Mapping):
            return {}
        state: dict[str, _AlertRevisions] = {}
        for event_id, raw_alerts in data.items():
            if not isinstance(raw_alerts, Mapping):
                continue
            alerts: _AlertRevisions = {}
            for fingerprint, raw_revision in raw_alerts.items():
                if (
                    isinstance(raw_revision, list)
                    and len(raw_revision) == 2
                    and isinstance(raw_revision[0], (int, float))
                    and isinstance(raw_revision[1], int)
                ):
                    alerts[str(fingerprint)] = (
                        float(raw_revision[0]),
                        raw_revision[1],
                    )
            state[str(event_id)] = alerts
        return state


@dataclass(frozen=True)
class AlertmanagerIntegration:
    r"""Mount Alertmanager webhooks on a Feishu gateway."""

    receive_id: str
    receive_id_type: str = "chat_id"
    path: str = "/alerts/alertmanager"
    store: AlertmanagerMessageStore | None = None
    card_builder: AlertmanagerCardBuilder | None = None

    def routes(self, context: GatewayContext) -> list[Route]:
        return [
            create_alertmanager_route(
                context.config,
                context.client,
                self.receive_id,
                receive_id_type=self.receive_id_type,
                path=self.path,
                store=self.store,
                card_builder=self.card_builder,
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
    card_builder: AlertmanagerCardBuilder | None = None,
) -> Route:
    r"""Create a service-authenticated Alertmanager webhook route.

    The route converts the standard Alertmanager webhook payload into a Feishu
    interactive card. Repeated notifications for one incident update the
    original card, while a single-alert incident with a new ``startsAt`` posts
    a new card so operators receive a fresh notification. The built-in stores
    serialize delivery within one process; multi-worker deployments require a
    gateway-level distributed delivery lock.
    """
    return Route(
        path,
        _alertmanager_endpoint(
            config,
            client,
            receive_id,
            receive_id_type=receive_id_type,
            store=store,
            card_builder=card_builder,
        ),
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
    if cluster and cluster.casefold() not in title.casefold():
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
            lines.append(f"- {_alert_instance_line(payload, alert)}")
        lines.extend(_alert_detail_lines(payload, alerts))
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


def _single_alert_alias(payload: Mapping[str, Any]) -> str:
    alerts = [alert for alert in payload.get("alerts", []) if isinstance(alert, Mapping)]
    if len(alerts) != 1:
        return ""
    return _single_alert_identity(payload, alerts[0])


def _single_alert_identity(payload: Mapping[str, Any], alert: Mapping[str, Any]) -> str:
    labels = {**_dict(payload.get("commonLabels")), **_dict(alert.get("labels"))}
    keys = ("alertname", "cluster", "node", "device", "service", "job", "instance")
    parts = [(key, _text(labels.get(key))) for key in keys]
    selected = [(key, value) for key, value in parts if value]
    if not selected:
        return ""
    return "{}:{" + ", ".join(f'{key}="{value}"' for key, value in selected) + "}"


def alertmanager_display_id(payload: Mapping[str, Any]) -> str:
    r"""Return a user-readable Alertmanager identifier for cards."""
    labels = _dict(payload.get("commonLabels"))
    alerts = [alert for alert in payload.get("alerts", []) if isinstance(alert, Mapping)]
    if len(alerts) == 1:
        alert = alerts[0]
        labels = {**labels, **_dict(alert.get("labels"))}
        cluster = _text(labels.get("cluster"))
        resource = _text(labels.get("node") or labels.get("service") or labels.get("instance") or labels.get("job"))
        device = _text(labels.get("device"))
        started_at = _display_timestamp(alert.get("startsAt"))
        concise = [part for part in (cluster, resource, device) if part]
        if concise and started_at:
            return f"{'/'.join(concise)}@{started_at}"

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
    card_builder: AlertmanagerCardBuilder | None,
) -> Callable[[Request], Awaitable[Response]]:
    event_store = store or InMemoryAlertmanagerStore()
    build_card = card_builder or build_alertmanager_card
    delivery_lock = asyncio.Lock()

    async def endpoint(request: Request) -> Response:
        try:
            require_service_capability(
                request,
                config.service_keys,
                config.service_capabilities,
            )
            payload = await read_json_object(request)
            event_id = alertmanager_event_id(payload)
            alias_id = _single_alert_alias(payload)
            delivery_id = _delivery_event_id(event_store, event_id, alias_id)
            delivery_id = _single_alert_incident_id(
                event_store,
                payload,
                delivery_id,
                alias_id,
            )
            async with delivery_lock:
                if _update_is_stale(event_store, delivery_id, payload):
                    return JSONResponse(
                        {
                            "action": "ignored_stale",
                            "event_id": delivery_id,
                            "message_id": event_store.get(delivery_id),
                        }
                    )
                card = build_card(payload)
                delivery = await upsert_interactive_card(
                    client,
                    delivery_id,
                    card,
                    receive_id,
                    receive_id_type=receive_id_type,
                    store=event_store,
                    uuid_prefix="am-",
                )
                _remember_alert_alias(event_store, event_id, alias_id, delivery.message_id, delivery_id)
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


def _delivery_event_id(store: AlertmanagerMessageStore, event_id: str, alias_id: str) -> str:
    if alias_id and not store.get(event_id) and store.get(alias_id):
        return alias_id
    return event_id


def _single_alert_incident_id(
    store: AlertmanagerMessageStore,
    payload: Mapping[str, Any],
    event_id: str,
    alias_id: str,
) -> str:
    alerts = [alert for alert in payload.get("alerts", []) if isinstance(alert, Mapping)]
    if len(alerts) != 1:
        return event_id
    starts_at = _text(alerts[0].get("startsAt"))
    if not starts_at:
        return event_id
    incident_id = f"{alias_id or event_id}:startsAt={starts_at}"
    if _text(payload.get("status")).lower() == "resolved" and not store.get(incident_id):
        return event_id
    return incident_id


def _remember_alert_alias(
    store: AlertmanagerMessageStore, event_id: str, alias_id: str, message_id: str | None, delivery_id: str
) -> None:
    if not alias_id or not message_id:
        return
    store.set(event_id, message_id)
    store.set(alias_id, message_id)
    revisions = store.get_alert_revisions(delivery_id)
    if revisions:
        store.set_alert_revisions(event_id, revisions)
        store.set_alert_revisions(alias_id, revisions)


def _update_is_stale(
    store: AlertmanagerMessageStore,
    event_id: str,
    payload: Mapping[str, Any],
) -> bool:
    incoming = _alert_revisions(payload)
    payload_status = _text(payload.get("status")).lower()
    if incoming is None or payload_status not in {"firing", "resolved"}:
        return False

    current = store.get_alert_revisions(event_id)
    merged = dict(current)
    stale = False
    for fingerprint, revision in incoming.items():
        previous = current.get(fingerprint)
        if previous is not None and revision < previous:
            stale = True
            continue
        merged[fingerprint] = revision

    store.set_alert_revisions(event_id, merged)
    merged_status = "firing" if any(lifecycle == 0 for _, lifecycle in merged.values()) else "resolved"
    return stale or payload_status != merged_status


def _alert_revisions(payload: Mapping[str, Any]) -> _AlertRevisions | None:
    alerts = [alert for alert in payload.get("alerts", []) if isinstance(alert, Mapping)]
    if not alerts:
        return None

    revisions: _AlertRevisions = {}
    for alert in alerts:
        fingerprint = _text(alert.get("fingerprint"))
        raw_started_at = _text(alert.get("startsAt"))
        status = _text(alert.get("status")).lower()
        if not fingerprint or not raw_started_at or status not in {"firing", "resolved"}:
            return None
        try:
            started_at = datetime.fromisoformat(raw_started_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        revisions[fingerprint] = (
            started_at.timestamp(),
            1 if status == "resolved" else 0,
        )
    return revisions


def _alert_title(payload: Mapping[str, Any]) -> str:
    labels = _dict(payload.get("commonLabels"))
    annotations = _dict(payload.get("commonAnnotations"))
    return (
        _text(annotations.get("title"))
        or _text(annotations.get("summary"))
        or _text(labels.get("alertname"))
        or "Alertmanager alert"
    )


def _alert_instance_line(payload: Mapping[str, Any], alert: Mapping[str, Any]) -> str:
    labels = {**_dict(payload.get("commonLabels")), **_dict(alert.get("labels"))}
    annotations = _dict(alert.get("annotations"))
    node = _text(labels.get("node") or labels.get("instance") or labels.get("service") or labels.get("job"))
    internal_ip = _text(labels.get("internal_ip") or annotations.get("node_ip"))
    if node and internal_ip and internal_ip != node:
        return f"`{node}` (`{internal_ip}`)"
    if node:
        return f"`{node}`"
    if internal_ip:
        return f"`{internal_ip}`"
    return "alert"


def _alert_detail_lines(payload: Mapping[str, Any], alerts: list[dict[str, Any]]) -> list[str]:
    common_annotations = _dict(payload.get("commonAnnotations"))
    details: list[str] = []
    impacts: list[str] = []
    actions: list[str] = []
    sources: list[tuple[str, str]] = []

    for alert in alerts[:8]:
        annotations = {**common_annotations, **_dict(alert.get("annotations"))}
        detail, impact, action = _description_sections(_text(annotations.get("description")))
        detail = _text(annotations.get("details")) or detail
        impact = _sentence_case(_text(annotations.get("impact")) or impact)
        action = _sentence_case(_text(annotations.get("action")) or action)
        if detail and detail not in details:
            details.append(detail)
        if impact and impact not in impacts:
            impacts.append(impact)
        if action and action not in actions:
            actions.append(action)

        source = _text(alert.get("generatorURL"))
        if source and all(url != source for _, url in sources):
            labels = {**_dict(payload.get("commonLabels")), **_dict(alert.get("labels"))}
            source_name = _text(labels.get("node") or labels.get("instance"))
            sources.append((source_name, source))

    lines: list[str] = []
    for heading, values in (("Details", details), ("Impact", impacts), ("Action", actions)):
        if not values:
            continue
        lines.extend(("", f"**{heading}**:", "\n".join(values)))
    if sources:
        lines.append("")
        if len(sources) == 1:
            lines.append(f"[Source]({sources[0][1]})")
        else:
            lines.append(" · ".join(f"[{name or 'Source'}]({url})" for name, url in sources))
    return lines


def _description_sections(description: str) -> tuple[str, str, str]:
    detail = description.strip()
    impact = ""
    action = ""
    if " Impact: " in detail:
        detail, remainder = detail.split(" Impact: ", 1)
        if " Action: " in remainder:
            impact, action = remainder.split(" Action: ", 1)
        else:
            impact = remainder
    elif " Action: " in detail:
        detail, action = detail.split(" Action: ", 1)
    return detail.strip(), impact.strip(), action.strip()


def _sentence_case(value: str) -> str:
    if not value:
        return ""
    return value[0].upper() + value[1:]


def _display_timestamp(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    try:
        timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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
