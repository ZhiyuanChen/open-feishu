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

from collections.abc import Mapping
from typing import Any

from feishu.agent.bundles import BundleContext
from feishu.agent.result import ToolResult
from feishu.agent.tools import Tool, ToolRegistry

from ._access import resolve_operational_access
from .slurm import resolve_slurm_client


class OpsBundle:
    r"""运营健康聚合 bundle，汇总按请求者身份收窄的只读状态。"""

    def register(self, registry: ToolRegistry, context: BundleContext) -> None:
        registry.add(
            Tool(
                name="get_operational_health",
                description="汇总按请求者身份收窄的只读状态，判断平台运营状态是否健康。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "max_jobs": {"type": "integer", "description": "最多返回多少个 Slurm 作业样本，默认 20。"},
                        "max_unhealthy_nodes": {
                            "type": "integer",
                            "description": "最多返回多少个异常节点样本，默认 20。",
                        },
                    },
                    "additionalProperties": False,
                },
                handler=lambda max_jobs=20, max_unhealthy_nodes=20: _get_operational_health(
                    context,
                    max_jobs=max_jobs,
                    max_unhealthy_nodes=max_unhealthy_nodes,
                ),
            )
        )


async def _get_operational_health(
    context: BundleContext,
    *,
    max_jobs: int = 20,
    max_unhealthy_nodes: int = 20,
) -> Any:
    access = await resolve_operational_access(service="运营状态")
    if isinstance(access, ToolResult):
        return access

    sources: dict[str, Any] = {}
    issues: list[dict[str, Any]] = []
    configured_sources = 0

    slurm_client = resolve_slurm_client(context, user=access.username)
    if slurm_client is None:
        sources["slurm"] = {"configured": False}
    else:
        configured_sources += 1
        await _collect_slurm(
            slurm_client,
            sources,
            issues,
            max_jobs=max_jobs,
            max_unhealthy_nodes=max_unhealthy_nodes,
        )

    errors = [source for source in sources.values() if isinstance(source, Mapping) and source.get("error")]
    if issues:
        status = "degraded"
    elif errors:
        status = "partial"
    elif configured_sources:
        status = "ok"
    else:
        status = "unknown"
    return {"status": status, "issues": issues, "sources": sources}


async def _collect_slurm(
    client: Any,
    sources: dict[str, Any],
    issues: list[dict[str, Any]],
    *,
    max_jobs: int,
    max_unhealthy_nodes: int,
) -> None:
    try:
        status = await client.cluster_status(max_jobs=max_jobs, max_unhealthy_nodes=max_unhealthy_nodes)
    except Exception as exc:  # noqa: BLE001 - one source failure should not hide other cluster facts
        sources["slurm"] = {"configured": True, "error": _error_detail(exc)}
        return
    unhealthy = _nested_list(status, "nodes", "unhealthy")
    sources["slurm"] = {"configured": True, "status": status}
    if unhealthy:
        issues.append({"source": "slurm", "kind": "unhealthy_nodes", "count": len(unhealthy)})


def _nested_list(payload: Any, *keys: str) -> list[Any]:
    current = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return []
        current = current.get(key)
    return current if isinstance(current, list) else []


def _error_detail(exc: Exception) -> str:
    return type(exc).__name__


__all__ = ["OpsBundle"]
