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
import base64
import hashlib
import hmac
import json
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from feishu.agent.bundles import BundleContext
from feishu.agent.context import current_tool_context
from feishu.agent.result import ToolOutcome, ToolResult
from feishu.agent.tools import Tool, ToolRegistry

from ._access import OperationalAccess, bool_config, normalize_identity, resolve_operational_access

_BAD_NODE_STATES = {
    "down",
    "drain",
    "drained",
    "draining",
    "fail",
    "failing",
    "future",
    "no_respond",
    "not_resp",
    "power_down",
    "powered_down",
    "unknown",
}


class SlurmBundle:
    r"""Slurm 只读状态 bundle，通过配置的 Slurm 客户端查询节点、队列、分区与集群概览。"""

    def register(self, registry: ToolRegistry, context: BundleContext) -> None:
        registry.add(
            Tool(
                name="get_slurm_cluster_status",
                description="通过配置的 Slurm 客户端读取集群节点、分区与队列的只读状态摘要。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "max_jobs": {"type": "integer", "description": "摘要中最多返回多少个作业样本，默认 20。"},
                        "max_unhealthy_nodes": {
                            "type": "integer",
                            "description": "摘要中最多返回多少个异常节点样本，默认 20。",
                        },
                    },
                    "additionalProperties": False,
                },
                handler=lambda max_jobs=20, max_unhealthy_nodes=20: _get_cluster_status(
                    context, max_jobs=max_jobs, max_unhealthy_nodes=max_unhealthy_nodes
                ),
            )
        )
        registry.add(
            Tool(
                name="list_slurm_nodes",
                description="通过配置的 Slurm 客户端读取节点列表，可按状态做客户端侧过滤。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "state": {"type": "string", "description": "可选节点状态过滤，例如 down、drain、idle。"},
                        "max_results": {"type": "integer", "description": "最多返回多少个节点，默认 100。"},
                    },
                    "additionalProperties": False,
                },
                handler=lambda state=None, max_results=100: _list_nodes(context, state=state, max_results=max_results),
            )
        )
        registry.add(
            Tool(
                name="list_slurm_jobs",
                description="通过配置的 Slurm 客户端读取当前作业列表，可按用户或状态做客户端侧过滤。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "state": {"type": "string", "description": "可选作业状态过滤，例如 running、pending、failed。"},
                        "user": {"type": "string", "description": "可选提交用户过滤。"},
                        "max_results": {"type": "integer", "description": "最多返回多少个作业，默认 50。"},
                    },
                    "additionalProperties": False,
                },
                handler=lambda state=None, user=None, max_results=50: _list_jobs(
                    context, state=state, user=user, max_results=max_results
                ),
            )
        )
        registry.add(
            Tool(
                name="list_slurm_partitions",
                description="通过配置的 Slurm 客户端读取分区列表。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "max_results": {"type": "integer", "description": "最多返回多少个分区，默认 50。"},
                    },
                    "additionalProperties": False,
                },
                handler=lambda max_results=50: _list_partitions(context, max_results=max_results),
            )
        )


class SlurmRestdClient:
    r"""轻量的异步 slurmrestd HTTP 客户端，仅调用 Slurm REST 只读端点。"""

    def __init__(
        self,
        base_url: str,
        *,
        api_version: str = "v0.0.45",
        user: str | None = None,
        token: str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version.strip("/")
        self.user = user
        self.token = token
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.transport = transport

    async def cluster_status(self, *, max_jobs: int = 20, max_unhealthy_nodes: int = 20) -> dict[str, Any]:
        ping, nodes, partitions, jobs = await asyncio.gather(
            self.ping(),
            self._collection("nodes"),
            self._collection("partitions"),
            self._collection("jobs"),
        )
        compact_nodes = [_compact_node(node) for node in nodes]
        compact_jobs = [_compact_job(job) for job in jobs]
        return {
            "ping": ping,
            "nodes": {
                "count": len(compact_nodes),
                "by_state": _state_counts(compact_nodes),
                "unhealthy": _limit([node for node in compact_nodes if _is_unhealthy_node(node)], max_unhealthy_nodes),
            },
            "partitions": {
                "count": len(partitions),
                "by_state": _state_counts([_compact_partition(partition) for partition in partitions]),
                "items": _limit([_compact_partition(partition) for partition in partitions], 50),
            },
            "jobs": {
                "count": len(compact_jobs),
                "by_state": _state_counts(compact_jobs),
                "items": _limit(compact_jobs, max_jobs),
            },
        }

    async def ping(self) -> dict[str, Any]:
        payload = await self._request("GET", self._path("ping/"))
        payload_dict = payload if isinstance(payload, dict) else {}
        pings = payload_dict.get("pings")
        if isinstance(pings, list):
            return {"pings": pings}
        return payload_dict

    async def nodes(self, *, state: str | None = None, max_results: int = 100) -> dict[str, Any]:
        nodes = [_compact_node(item) for item in await self._collection("nodes")]
        filtered = [node for node in nodes if _matches_state(node, state)]
        return _limited_collection("nodes", filtered, max_results)

    async def jobs(
        self,
        *,
        state: str | None = None,
        user: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        jobs = [_compact_job(item) for item in await self._collection("jobs")]
        filtered = [job for job in jobs if _matches_state(job, state) and _matches_user(job, user)]
        return _limited_collection("jobs", filtered, max_results)

    async def partitions(self, *, max_results: int = 50) -> dict[str, Any]:
        partitions = [_compact_partition(item) for item in await self._collection("partitions")]
        return _limited_collection("partitions", partitions, max_results)

    async def _collection(self, name: str) -> list[dict[str, Any]]:
        payload = await self._request("GET", self._path(f"{name}/"))
        data = payload if isinstance(payload, dict) else {}
        items = data.get(name)
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Accept": "application/json", **self.headers}
        if self.user and "X-SLURM-USER-NAME" not in headers:
            headers["X-SLURM-USER-NAME"] = self.user
        if self.token and "X-SLURM-USER-TOKEN" not in headers:
            headers["X-SLURM-USER-TOKEN"] = self.token
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers=headers,
            transport=self.transport,
        ) as client:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

    def _path(self, suffix: str) -> str:
        return f"/slurm/{self.api_version}/{suffix.lstrip('/')}"


class SlurmWebGatewayClient:
    r"""基于集中式 Slurm-web gateway 的只读 Slurm 客户端。"""

    def __init__(
        self,
        base_url: str,
        *,
        cluster: str | Sequence[str],
        jwt_key_file: str,
        groups: Sequence[str] = (),
        audience: str = "slurm-web",
        token_ttl_seconds: int = 300,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        username: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.clusters = _clusters(cluster)
        self.cluster = self.clusters[0]
        self.jwt_key_file = jwt_key_file
        self.groups = tuple(groups) or tuple(f"cluster.{cluster_name}.user" for cluster_name in self.clusters)
        self.audience = audience
        self.token_ttl_seconds = token_ttl_seconds
        self.timeout = timeout
        self.transport = transport
        self.username = _text(username)

    def with_user(self, username: str) -> SlurmWebGatewayClient:
        return SlurmWebGatewayClient(
            self.base_url,
            cluster=self.clusters,
            jwt_key_file=self.jwt_key_file,
            groups=self.groups,
            audience=self.audience,
            token_ttl_seconds=self.token_ttl_seconds,
            timeout=self.timeout,
            transport=self.transport,
            username=username,
        )

    async def cluster_status(self, *, max_jobs: int = 20, max_unhealthy_nodes: int = 20) -> dict[str, Any]:
        ping, nodes, partitions, jobs = await asyncio.gather(
            self.ping(),
            self.nodes(max_results=10000),
            self.partitions(max_results=10000),
            self.jobs(max_results=10000),
        )
        compact_nodes = nodes["nodes"]
        compact_partitions = partitions["partitions"]
        compact_jobs = jobs["jobs"]
        return {
            "ping": ping,
            "nodes": {
                "count": len(compact_nodes),
                "by_state": _state_counts(compact_nodes),
                "unhealthy": _limit([node for node in compact_nodes if _is_unhealthy_node(node)], max_unhealthy_nodes),
            },
            "partitions": {
                "count": len(compact_partitions),
                "by_state": _state_counts(compact_partitions),
                "items": _limit(compact_partitions, 50),
            },
            "jobs": {
                "count": len(compact_jobs),
                "by_state": _state_counts(compact_jobs),
                "items": _limit(compact_jobs, max_jobs),
            },
        }

    async def ping(self) -> dict[str, Any]:
        payloads = await self._request_all("ping")
        if len(payloads) == 1:
            payload = next(iter(payloads.values()))
            return payload if isinstance(payload, dict) else {"payload": payload}
        return {"clusters": payloads}

    async def nodes(self, *, state: str | None = None, max_results: int = 100) -> dict[str, Any]:
        payloads = await self._request_all("nodes")
        nodes = [
            {**_compact_node(item), "cluster": cluster}
            for cluster, payload in payloads.items()
            for item in _payload_items(payload, "nodes")
        ]
        return _limited_collection("nodes", [node for node in nodes if _matches_state(node, state)], max_results)

    async def jobs(
        self,
        *,
        state: str | None = None,
        user: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        requested_user = _text(user) or self._requester_username()
        payloads = await self._request_all("jobs")
        jobs = [
            {**_compact_job(item), "cluster": cluster}
            for cluster, payload in payloads.items()
            for item in _payload_items(payload, "jobs")
        ]
        filtered = [job for job in jobs if _matches_state(job, state) and _matches_user(job, requested_user)]
        return _limited_collection("jobs", filtered, max_results)

    async def partitions(self, *, max_results: int = 50) -> dict[str, Any]:
        payloads = await self._request_all("partitions")
        partitions = [
            {**_compact_partition(item), "cluster": cluster}
            for cluster, payload in payloads.items()
            for item in _payload_items(payload, "partitions")
        ]
        return _limited_collection("partitions", partitions, max_results)

    async def _request_all(self, path: str) -> dict[str, Any]:
        results = await asyncio.gather(*(self._request(cluster, path) for cluster in self.clusters))
        return dict(zip(self.clusters, results, strict=True))

    async def _request(self, cluster: str, path: str) -> Any:
        token = self._token(self._requester_username())
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/api/agents/{cluster}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, transport=self.transport) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    def _requester_username(self) -> str:
        return self.username or _requester_username()

    def _token(self, username: str) -> str:
        now = int(time.time())
        key = Path(self.jwt_key_file).read_text(encoding="utf-8").strip()
        return _jwt_hs256(
            key,
            {
                "aud": self.audience,
                "sub": username,
                "groups": list(self.groups),
                "iat": now,
                "exp": now + self.token_ttl_seconds,
            },
        )


async def _get_cluster_status(context: BundleContext, *, max_jobs: int = 20, max_unhealthy_nodes: int = 20) -> Any:
    access = await _authorize_slurm(context)
    if isinstance(access, ToolResult):
        return access
    client = resolve_slurm_client(context, user=access.username)
    if client is None:
        return _not_configured("Slurm")
    try:
        return await client.cluster_status(max_jobs=max_jobs, max_unhealthy_nodes=max_unhealthy_nodes)
    except Exception as exc:  # noqa: BLE001 - external tool failures become structured tool results
        return _request_failed("Slurm", exc)


async def _list_nodes(context: BundleContext, *, state: str | None = None, max_results: int = 100) -> Any:
    access = await _authorize_slurm(context)
    if isinstance(access, ToolResult):
        return access
    client = resolve_slurm_client(context, user=access.username)
    if client is None:
        return _not_configured("Slurm")
    try:
        return await client.nodes(state=state, max_results=max_results)
    except Exception as exc:  # noqa: BLE001 - external tool failures become structured tool results
        return _request_failed("Slurm", exc)


async def _list_jobs(
    context: BundleContext,
    *,
    state: str | None = None,
    user: str | None = None,
    max_results: int = 50,
) -> Any:
    config = _slurm_config(context)
    access = await _authorize_slurm(context)
    if isinstance(access, ToolResult):
        return access
    user_result = _job_user_filter(config, access, requested_user=user)
    if isinstance(user_result, ToolResult):
        return user_result
    client = resolve_slurm_client(context, user=access.username)
    if client is None:
        return _not_configured("Slurm")
    try:
        return await client.jobs(state=state, user=user_result, max_results=max_results)
    except Exception as exc:  # noqa: BLE001 - external tool failures become structured tool results
        return _request_failed("Slurm", exc)


async def _list_partitions(context: BundleContext, *, max_results: int = 50) -> Any:
    access = await _authorize_slurm(context)
    if isinstance(access, ToolResult):
        return access
    client = resolve_slurm_client(context, user=access.username)
    if client is None:
        return _not_configured("Slurm")
    try:
        return await client.partitions(max_results=max_results)
    except Exception as exc:  # noqa: BLE001 - external tool failures become structured tool results
        return _request_failed("Slurm", exc)


async def _authorize_slurm(context: BundleContext) -> OperationalAccess | ToolResult:
    return await resolve_operational_access(service="Slurm")


def _job_user_filter(
    config: Mapping[str, Any],
    access: OperationalAccess,
    *,
    requested_user: str | None,
) -> str | ToolResult | None:
    if not bool_config(config.get("restrict_jobs_to_requester"), default=True):
        return requested_user
    requested = _text(requested_user)
    if requested and normalize_identity(requested) not in access.owner_aliases:
        return ToolResult(
            ToolOutcome.BLOCKED,
            content="只能查询请求者自己的 Slurm 作业",
            is_error=True,
        )
    if not access.username:
        return ToolResult(
            ToolOutcome.BLOCKED,
            content="无法从请求者身份解析 Slurm 用户名",
            is_error=True,
        )
    return access.username


def resolve_slurm_client(context: BundleContext, *, user: str | None = None) -> Any | None:
    injected = context.extra.get("slurm_client")
    if injected is not None:
        if user:
            bind = getattr(injected, "with_user", None)
            if bind is not None:
                return bind(user)
        return injected
    config = _slurm_config(context)
    if not config:
        return None
    injected = config.get("client")
    if injected is not None:
        if user:
            bind = getattr(injected, "with_user", None)
            if bind is not None:
                return bind(user)
        return injected
    gateway_url = _text(config.get("gateway_url") or config.get("slurmweb_gateway_url"))
    jwt_key_file = _text(config.get("jwt_key_file") or config.get("slurmweb_jwt_key_file"))
    cluster = config.get("cluster") or config.get("clusters") or config.get("slurmweb_cluster")
    if gateway_url and jwt_key_file and cluster:
        transport = config.get("transport")
        return SlurmWebGatewayClient(
            gateway_url,
            cluster=_cluster_value(cluster),
            jwt_key_file=jwt_key_file,
            groups=_sequence_value(config.get("groups") or config.get("slurmweb_groups")),
            audience=_text(config.get("audience")) or "slurm-web",
            token_ttl_seconds=_int(config.get("token_ttl_seconds"), default=300),
            timeout=_float(config.get("timeout"), default=10.0),
            transport=transport if isinstance(transport, httpx.AsyncBaseTransport) else None,
            username=_text(user) or None,
        )
    base_url = _text(config.get("base_url") or config.get("url"))
    if not base_url:
        return None
    timeout = _float(config.get("timeout"), default=10.0)
    headers_value = config.get("headers")
    headers = headers_value if isinstance(headers_value, Mapping) else None
    return SlurmRestdClient(
        base_url,
        api_version=_text(config.get("api_version") or config.get("version")) or "v0.0.45",
        user=_text(user) or _text(config.get("user") or config.get("username")) or None,
        token=_text(config.get("token") or config.get("jwt_token")) or None,
        headers={str(key): str(value) for key, value in (headers or {}).items()},
        timeout=timeout,
    )


def _slurm_config(context: BundleContext) -> Mapping[str, Any]:
    config = context.extra.get("slurm")
    return config if isinstance(config, Mapping) else {}


def _compact_node(item: dict[str, Any]) -> dict[str, Any]:
    name = _text(item.get("name") or item.get("hostname") or item.get("node_name"))
    states = _states(item.get("state") or item.get("states") or item.get("state_flags") or item.get("state_string"))
    return _drop_empty(
        {
            "name": name,
            "states": states,
            "partitions": _string_list(item.get("partitions") or item.get("partition")),
            "cpus": item.get("cpus"),
            "alloc_cpus": item.get("alloc_cpus"),
            "alloc_idle_cpus": item.get("alloc_idle_cpus"),
            "real_memory": item.get("real_memory"),
            "gres": _text(item.get("gres")),
            "reason": _text(item.get("reason")),
        }
    )


def _compact_job(item: dict[str, Any]) -> dict[str, Any]:
    states = _states(item.get("job_state") or item.get("state") or item.get("states"))
    return _drop_empty(
        {
            "job_id": item.get("job_id") or item.get("id"),
            "name": _text(item.get("name")),
            "user": _text(item.get("user_name") or item.get("user")),
            "account": _text(item.get("account")),
            "partition": _text(item.get("partition")),
            "states": states,
            "nodes": _text(item.get("nodes") or item.get("node_list")),
            "reason": _text(item.get("state_reason") or item.get("reason")),
            "submit_time": item.get("submit_time"),
            "start_time": item.get("start_time"),
            "end_time": item.get("end_time"),
            "time_limit": item.get("time_limit"),
            "tres_alloc": _text(item.get("tres_alloc_str") or item.get("tres_alloc")),
            "tres_req": _text(item.get("tres_req_str") or item.get("tres_req")),
        }
    )


def _compact_partition(item: dict[str, Any]) -> dict[str, Any]:
    nodes = item.get("nodes")
    node_info = nodes if isinstance(nodes, dict) else {}
    return _drop_empty(
        {
            "name": _text(item.get("name") or item.get("partition")),
            "states": _states(item.get("state") or item.get("states")),
            "total_nodes": item.get("total_nodes") or node_info.get("total"),
            "total_cpus": item.get("total_cpus"),
            "nodes": _text(node_info.get("configured") or item.get("nodes")),
            "default_time": item.get("default_time"),
            "maximum_time": item.get("maximum_time") or item.get("max_time"),
        }
    )


def _limited_collection(name: str, items: list[dict[str, Any]], max_results: int) -> dict[str, Any]:
    limit = _limit_value(max_results, default=50, ceiling=500)
    return {
        name: items[:limit],
        "count": len(items),
        "truncated": len(items) > limit,
    }


def _limit(items: list[dict[str, Any]], max_results: int) -> list[dict[str, Any]]:
    return items[: _limit_value(max_results, default=20, ceiling=500)]


def _limit_value(value: Any, *, default: int, ceiling: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, ceiling))


def _state_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in items:
        states = item.get("states")
        if isinstance(states, list) and states:
            for state in states:
                counter[str(state)] += 1
        else:
            counter["unknown"] += 1
    return dict(sorted(counter.items()))


def _is_unhealthy_node(node: dict[str, Any]) -> bool:
    states = node.get("states")
    if not isinstance(states, list) or not states:
        return True
    return bool(_BAD_NODE_STATES.intersection({str(state).lower() for state in states}))


def _matches_state(item: dict[str, Any], state: str | None) -> bool:
    desired = _text(state).lower()
    if not desired:
        return True
    states = item.get("states")
    return isinstance(states, list) and desired in {str(item_state).lower() for item_state in states}


def _matches_user(item: dict[str, Any], user: str | None) -> bool:
    desired = _text(user).lower()
    return not desired or _text(item.get("user")).lower() == desired


def _states(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = [_text(item) for item in value]
    else:
        text = _text(value)
        for separator in ("+", ",", "|", "/"):
            text = text.replace(separator, " ")
        raw = text.split()
    return [state.lower() for state in raw if state]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    return [item.strip() for item in text.split(",") if item.strip()] if text else []


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [])}


def _requester_username() -> str:
    user = current_tool_context().requesting_user()
    return _text(user.get("user_id"))


def _clusters(value: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        items: Sequence[str] = value.split(",")
    else:
        items = value
    clusters = tuple(_text(item) for item in items if _text(item))
    if not clusters:
        raise ValueError("at least one Slurm-web cluster is required")
    return clusters


def _cluster_value(value: Any) -> str | Sequence[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence):
        return [str(item) for item in value]
    return str(value)


def _sequence_value(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, Sequence):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),)


def _payload_items(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _jwt_hs256(key: str, payload: dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_b64json(header)}.{_b64json(payload)}"
    signature = hmac.new(key.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(signature)}"


def _b64json(value: dict[str, Any]) -> str:
    return _b64(json.dumps(value, separators=(",", ":")).encode())


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _not_configured(service: str) -> ToolResult:
    return ToolResult(
        ToolOutcome.BLOCKED,
        content=f"{service} 客户端未配置",
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


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _float(value: Any, *, default: float) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, *, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = ["SlurmBundle", "SlurmRestdClient", "SlurmWebGatewayClient"]
