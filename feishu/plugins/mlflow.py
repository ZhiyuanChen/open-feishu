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


class MLflowBundle:
    r"""无额外依赖的 MLflow / tracker run 载荷归一化工具 bundle。"""

    def register(self, registry: ToolRegistry, context: BundleContext) -> None:
        registry.add(
            Tool(
                name="normalize_mlflow_run",
                description="把 MLflow run 或 tracker 事件载荷归一化为紧凑 run 事实。",
                input_schema={
                    "type": "object",
                    "properties": {"payload": {"type": "object", "description": "MLflow run 或 tracker 事件 JSON。"}},
                    "required": ["payload"],
                    "additionalProperties": False,
                },
                handler=lambda payload: normalize_mlflow_run(payload),
            )
        )


def normalize_mlflow_run(payload: dict[str, Any]) -> dict[str, Any]:
    r"""把 MLflow run 或 tracker 事件载荷归一化为适合模型消费的 run 事实。"""
    run_value = payload.get("run")
    run = dict(run_value) if isinstance(run_value, dict) else payload
    info_value = run.get("info")
    info = dict(info_value) if isinstance(info_value, dict) else run
    data = _dict(run.get("data"))
    tags = _dict(data.get("tags"))
    return {
        "run_id": _text(info.get("run_id") or info.get("run_uuid") or run.get("run_id")),
        "experiment_id": _text(info.get("experiment_id") or run.get("experiment_id")),
        "status": _text(info.get("status") or run.get("status")),
        "name": _text(tags.get("mlflow.runName") or tags.get("run_name") or info.get("run_name")),
        "artifact_uri": _text(info.get("artifact_uri") or run.get("artifact_uri")),
        "metrics": _dict(data.get("metrics") or run.get("metrics")),
        "params": _dict(data.get("params") or run.get("params")),
        "tags": _dict(tags or run.get("tags")),
    }


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""
