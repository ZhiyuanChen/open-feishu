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

import base64
from collections.abc import Mapping
from typing import Any

import httpx

from feishu.agent.bundles import BundleContext
from feishu.agent.result import ToolOutcome, ToolResult
from feishu.agent.tools import Tool, ToolRegistry

from ._access import OperationalAccess, normalize_identity, resolve_operational_access


class MLflowBundle:
    r"""MLflow / tracker 工具 bundle：run 载荷归一化，外加（注入 MLflow 客户端后）只读的 experiment 与 run 查询工具。"""

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
        registry.add(
            Tool(
                name="search_mlflow_experiments",
                description="搜索 MLflow experiments，用于定位训练任务所在实验。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "实验名称关键词。"},
                        "max_results": {"type": "integer", "description": "最多返回多少个实验，默认 20。"},
                    },
                    "additionalProperties": False,
                },
                handler=lambda query=None, max_results=20: _search_experiments(
                    context, query=query, max_results=max_results
                ),
            )
        )
        registry.add(
            Tool(
                name="search_mlflow_runs",
                description="搜索 MLflow runs，用于查看训练任务状态、指标、参数和最近运行。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "experiment_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "MLflow experiment_id 列表。",
                        },
                        "experiment_name": {"type": "string", "description": "实验名称；未给 ids 时可用。"},
                        "filter": {"type": "string", "description": "MLflow runs/search filter 字符串。"},
                        "max_results": {"type": "integer", "description": "最多返回多少个 run，默认 10。"},
                        "order_by": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "MLflow order_by 列表。",
                        },
                    },
                    "additionalProperties": False,
                },
                handler=lambda experiment_ids=None, experiment_name=None, filter=None, max_results=10, order_by=None: (
                    _search_runs(
                        context,
                        experiment_ids=experiment_ids,
                        experiment_name=experiment_name,
                        filter=filter,
                        max_results=max_results,
                        order_by=order_by,
                    )
                ),
            )
        )
        registry.add(
            Tool(
                name="get_mlflow_run",
                description="读取单个 MLflow run 的状态、指标、参数、标签和 artifact URI。",
                input_schema={
                    "type": "object",
                    "properties": {"run_id": {"type": "string", "description": "MLflow run_id。"}},
                    "required": ["run_id"],
                    "additionalProperties": False,
                },
                handler=lambda run_id: _get_run(context, run_id=run_id),
            )
        )


class MLflowClient:
    r"""轻量的异步 MLflow Tracking REST 客户端，用于只读地查看 run。"""

    def __init__(
        self,
        base_url: str,
        *,
        username: str | None = None,
        api_token: str | None = None,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.api_token = api_token
        self.timeout = timeout
        self.transport = transport

    async def search_experiments(self, *, query: str | None = None, max_results: int = 20) -> list[dict[str, Any]]:
        payload = await self._request(
            "POST",
            "/api/2.0/mlflow/experiments/search",
            json={"max_results": max(1, min(int(max_results), 100)), "view_type": "ALL"},
        )
        payload_dict = payload if isinstance(payload, dict) else {}
        experiments_value = payload_dict.get("experiments")
        experiments = experiments_value if isinstance(experiments_value, list) else []
        items = [_compact_experiment(item) for item in experiments if isinstance(item, dict)]
        if query:
            lowered = query.lower()
            items = [item for item in items if lowered in item.get("name", "").lower()]
        return items

    async def search_runs(
        self,
        *,
        experiment_ids: list[str] | None = None,
        experiment_name: str | None = None,
        filter: str | None = None,
        max_results: int = 10,
        order_by: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        ids = list(experiment_ids or [])
        if not ids and experiment_name:
            experiment = await self._experiment_by_name(experiment_name)
            experiment_id = _text(experiment.get("experiment_id"))
            if experiment_id:
                ids = [experiment_id]
        if not ids:
            experiments = await self.search_experiments(max_results=100)
            ids = [item["experiment_id"] for item in experiments if item["experiment_id"]]
        payload: dict[str, Any] = {
            "experiment_ids": ids,
            "max_results": max(1, min(int(max_results), 100)),
            "run_view_type": "ALL",
        }
        if filter:
            payload["filter"] = filter
        if order_by:
            payload["order_by"] = order_by
        result = await self._request("POST", "/api/2.0/mlflow/runs/search", json=payload)
        result_dict = result if isinstance(result, dict) else {}
        runs_value = result_dict.get("runs")
        runs = runs_value if isinstance(runs_value, list) else []
        return [normalize_mlflow_run(run) for run in runs if isinstance(run, dict)]

    async def get_run(self, run_id: str) -> dict[str, Any]:
        payload = await self._request("GET", "/api/2.0/mlflow/runs/get", params={"run_id": run_id})
        return normalize_mlflow_run(payload)

    async def _experiment_by_name(self, name: str) -> dict[str, Any]:
        payload = await self._request(
            "GET",
            "/api/2.0/mlflow/experiments/get-by-name",
            params={"experiment_name": name},
        )
        experiment = payload.get("experiment") if isinstance(payload, dict) else {}
        return experiment if isinstance(experiment, dict) else {}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Accept": "application/json"}
        if self.username and self.api_token:
            encoded = base64.b64encode(f"{self.username}:{self.api_token}".encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        elif self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers=headers,
            transport=self.transport,
        ) as client:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()


async def _search_experiments(context: BundleContext, *, query: str | None = None, max_results: int = 20) -> Any:
    client = _resolve_mlflow_client(context)
    if client is None:
        return _not_configured("MLflow")
    access = await _resolve_requester_access(context)
    if isinstance(access, ToolResult):
        return access
    try:
        experiments = await client.search_experiments(query=query, max_results=max_results)
        experiments = await _filter_experiments_for_requester(client, experiments, access)
    except Exception as exc:  # noqa: BLE001 - external tool failures become structured tool results
        return _request_failed("MLflow", exc)
    result: dict[str, Any] = {"experiments": experiments}
    result["filtered_by_requester"] = True
    return result


async def _search_runs(
    context: BundleContext,
    *,
    experiment_ids: list[str] | None = None,
    experiment_name: str | None = None,
    filter: str | None = None,
    max_results: int = 10,
    order_by: list[str] | None = None,
) -> Any:
    client = _resolve_mlflow_client(context)
    if client is None:
        return _not_configured("MLflow")
    access = await _resolve_requester_access(context)
    if isinstance(access, ToolResult):
        return access
    try:
        runs = await client.search_runs(
            experiment_ids=experiment_ids,
            experiment_name=experiment_name,
            filter=filter,
            max_results=max_results,
            order_by=order_by,
        )
    except Exception as exc:  # noqa: BLE001 - external tool failures become structured tool results
        return _request_failed("MLflow", exc)
    result: dict[str, Any] = {"runs": _filter_runs_for_requester(runs, access, max_results=max_results)}
    result["filtered_by_requester"] = True
    return result


async def _get_run(context: BundleContext, *, run_id: str) -> Any:
    client = _resolve_mlflow_client(context)
    if client is None:
        return _not_configured("MLflow")
    access = await _resolve_requester_access(context)
    if isinstance(access, ToolResult):
        return access
    try:
        run = await client.get_run(run_id)
    except Exception as exc:  # noqa: BLE001 - external tool failures become structured tool results
        return _request_failed("MLflow", exc)
    if not _run_visible_to_requester(run, access):
        return _requester_blocked()
    return {"run": run}


def _resolve_mlflow_client(context: BundleContext) -> Any | None:
    injected = context.extra.get("mlflow_client")
    if injected is not None:
        return injected
    config = context.extra.get("mlflow")
    if not isinstance(config, dict):
        return None
    injected = config.get("client")
    if injected is not None:
        return injected
    base_url = _text(config.get("base_url") or config.get("tracking_uri") or config.get("url"))
    if not base_url:
        return None
    return MLflowClient(
        base_url,
        username=_text(config.get("username")) or None,
        api_token=_text(config.get("api_token") or config.get("token")) or None,
        timeout=_float(config.get("timeout"), default=10.0),
    )


async def _resolve_requester_access(context: BundleContext) -> OperationalAccess | ToolResult:
    return await resolve_operational_access(service="MLflow")


def _filter_runs_for_requester(
    runs: list[dict[str, Any]],
    access: OperationalAccess | None,
    *,
    max_results: int | None = None,
) -> list[dict[str, Any]]:
    if access is None:
        return runs
    filtered = [run for run in runs if _run_visible_to_requester(run, access)]
    if max_results is None:
        return filtered
    return filtered[: max(1, min(int(max_results), 100))]


async def _filter_experiments_for_requester(
    client: Any,
    experiments: list[dict[str, Any]],
    access: OperationalAccess,
) -> list[dict[str, Any]]:
    visible = []
    for experiment in experiments:
        experiment_id = _text(experiment.get("experiment_id"))
        if not experiment_id:
            continue
        runs = await client.search_runs(experiment_ids=[experiment_id], max_results=100)
        if _filter_runs_for_requester(runs, access):
            visible.append(experiment)
    return visible


def _run_visible_to_requester(run: dict[str, Any], access: OperationalAccess) -> bool:
    owner = _run_owner(run)
    return bool(owner and owner in access.owner_aliases)


def _run_owner(run: Mapping[str, Any]) -> str:
    tags = _dict(run.get("tags"))
    for key in ("mlflow.user", "owner", "owner_email", "username", "user"):
        value = _text(tags.get(key) or run.get(key))
        if value:
            return normalize_identity(value)
    return ""


def normalize_mlflow_run(payload: dict[str, Any]) -> dict[str, Any]:
    r"""把 MLflow run 或 tracker 事件载荷归一化为适合模型消费的 run 事实。"""
    run_value = payload.get("run")
    run = dict(run_value) if isinstance(run_value, dict) else payload
    info_value = run.get("info")
    info = dict(info_value) if isinstance(info_value, dict) else run
    data = _dict(run.get("data"))
    tags = _key_value_dict(data.get("tags") or run.get("tags"))
    return {
        "run_id": _text(info.get("run_id") or info.get("run_uuid") or run.get("run_id")),
        "experiment_id": _text(info.get("experiment_id") or run.get("experiment_id")),
        "status": _text(info.get("status") or run.get("status")),
        "name": _text(tags.get("mlflow.runName") or tags.get("run_name") or info.get("run_name")),
        "artifact_uri": _text(info.get("artifact_uri") or run.get("artifact_uri")),
        "metrics": _key_value_dict(data.get("metrics") or run.get("metrics")),
        "params": _key_value_dict(data.get("params") or run.get("params")),
        "tags": tags,
    }


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _key_value_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, list):
        return {}
    result = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        key = _text(item.get("key"))
        if key:
            result[key] = item.get("value")
    return result


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _float(value: Any, *, default: float) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _compact_experiment(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "experiment_id": _text(item.get("experiment_id")),
        "name": _text(item.get("name")),
        "lifecycle_stage": _text(item.get("lifecycle_stage")),
        "artifact_location": _text(item.get("artifact_location")),
    }


def _not_configured(service: str) -> ToolResult:
    return ToolResult(
        ToolOutcome.BLOCKED,
        content=f"{service} 客户端未配置",
        is_error=True,
    )


def _requester_blocked() -> ToolResult:
    return ToolResult(
        ToolOutcome.BLOCKED,
        content="请求者无权查看该 MLflow run",
        is_error=True,
    )


def _request_failed(service: str, exc: Exception) -> ToolResult:
    detail = type(exc).__name__
    if isinstance(exc, httpx.HTTPStatusError):
        detail = f"HTTP {exc.response.status_code}"
    return ToolResult(
        ToolOutcome.FAILED,
        content=f"{service} 请求失败：{detail}",
        is_error=True,
    )
