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

from typing import Any

from feishu.agent.bundles import BundleContext
from feishu.agent.tools import Tool, ToolRegistry


class GrafanaBundle:
    r"""无额外依赖的 Grafana / Alertmanager 告警载荷归一化工具 bundle。"""

    def register(self, registry: ToolRegistry, context: BundleContext) -> None:
        registry.add(
            Tool(
                name="normalize_grafana_alerts",
                description="把 Grafana 或 Alertmanager 告警 webhook 载荷归一化为紧凑告警事实。",
                input_schema={
                    "type": "object",
                    "properties": {"payload": {"type": "object", "description": "Grafana/Alertmanager webhook JSON。"}},
                    "required": ["payload"],
                    "additionalProperties": False,
                },
                handler=lambda payload: normalize_grafana_alerts(payload),
            )
        )


def normalize_grafana_alerts(payload: dict[str, Any]) -> dict[str, Any]:
    r"""把 Grafana / Alertmanager webhook 载荷归一化为适合模型消费的告警事实。"""
    alerts_value = payload.get("alerts")
    alerts = alerts_value if isinstance(alerts_value, list) else []
    normalized = [_alert(item) for item in alerts if isinstance(item, dict)]
    return {
        "status": _text(payload.get("status")) or _aggregate_status(normalized),
        "alert_count": len(normalized),
        "alerts": normalized,
    }


def _alert(alert: dict[str, Any]) -> dict[str, Any]:
    labels_value = alert.get("labels")
    labels = dict(labels_value) if isinstance(labels_value, dict) else {}
    annotations_value = alert.get("annotations")
    annotations = dict(annotations_value) if isinstance(annotations_value, dict) else {}
    return {
        "status": _text(alert.get("status")),
        "title": _text(labels.get("alertname") or annotations.get("summary") or annotations.get("title")),
        "summary": _text(annotations.get("summary") or annotations.get("description")),
        "cluster": _text(labels.get("cluster")),
        "service": _text(labels.get("service") or labels.get("job")),
        "severity": _text(labels.get("severity")),
        "url": _text(alert.get("generatorURL") or alert.get("dashboardURL") or alert.get("panelURL")),
        "labels": {str(key): _text(value) for key, value in labels.items()},
    }


def _aggregate_status(alerts: list[dict[str, Any]]) -> str:
    if any(alert.get("status") == "firing" for alert in alerts):
        return "firing"
    if alerts:
        return "resolved"
    return "unknown"


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""
