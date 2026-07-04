from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx

from feishu.agent.bundles import BundleContext, build_tool_registry
from feishu.agent.context import ToolContext, use_tool_context
from feishu.agent.result import ToolOutcome
from feishu.plugins import SlurmWebGatewayClient, register_bundled_plugins


def test_slurmweb_gateway_client(tmp_path: Path) -> None:
    key = tmp_path / "jwt.key"
    key.write_text("gateway-key", encoding="utf-8")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"].startswith("Bearer ")
        if request.url.path == "/api/agents/a800-1/ping":
            return httpx.Response(200, json={"versions": {"api": "0.0.44"}})
        if request.url.path == "/api/agents/a800-1/nodes":
            return httpx.Response(200, json=[{"name": "gpu-1", "state": ["DOWN"], "reason": "maintenance"}])
        if request.url.path == "/api/agents/a800-1/partitions":
            return httpx.Response(200, json=[{"name": "gpu", "state": ["UP"], "total_nodes": 1}])
        if request.url.path == "/api/agents/a800-1/jobs":
            return httpx.Response(
                200,
                json=[
                    {"job_id": 1, "user_name": "qinghanw313", "job_state": ["RUNNING"]},
                    {"job_id": 2, "user_name": "other", "job_state": ["RUNNING"]},
                ],
            )
        raise AssertionError(request.url.path)

    async def run() -> dict[str, Any]:
        client = SlurmWebGatewayClient(
            "http://slurm-web-gateway:5011",
            cluster="a800-1",
            jwt_key_file=str(key),
            groups=("cluster.a800-1.user",),
            transport=httpx.MockTransport(handler),
        )
        with use_tool_context(ToolContext(user={"user_id": "qinghanw313"})):
            status = await client.cluster_status(max_jobs=10, max_unhealthy_nodes=10)
            jobs = await client.jobs(max_results=10)
        return {"status": status, "jobs": jobs}

    result = asyncio.run(run())

    assert result["status"]["jobs"]["items"] == [
        {"job_id": 1, "user": "qinghanw313", "states": ["running"], "cluster": "a800-1"}
    ]
    assert result["jobs"]["jobs"] == [{"job_id": 1, "user": "qinghanw313", "states": ["running"], "cluster": "a800-1"}]
    token = requests[0].headers["authorization"].removeprefix("Bearer ")
    payload = json.loads(_decode_segment(token.split(".")[1]))
    assert payload["aud"] == "slurm-web"
    assert payload["sub"] == "qinghanw313"
    assert payload["groups"] == ["cluster.a800-1.user"]


def test_slurmweb_gateway_client_with_user(tmp_path: Path) -> None:
    key = tmp_path / "jwt.key"
    key.write_text("gateway-key", encoding="utf-8")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"versions": {"api": "0.0.44"}})

    async def run() -> None:
        client = SlurmWebGatewayClient(
            "http://slurm-web-gateway:5011",
            cluster="a800-1",
            jwt_key_file=str(key),
            transport=httpx.MockTransport(handler),
        ).with_user("qinghanw313")
        with use_tool_context(ToolContext(user={"open_id": "ou_qing"})):
            await client.ping()

    asyncio.run(run())

    token = requests[0].headers["authorization"].removeprefix("Bearer ")
    payload = json.loads(_decode_segment(token.split(".")[1]))
    assert payload["sub"] == "qinghanw313"


def test_slurmweb_gateway_client_multiple_clusters(tmp_path: Path) -> None:
    key = tmp_path / "jwt.key"
    key.write_text("gateway-key", encoding="utf-8")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/api/agents/a800-1/ping":
            return httpx.Response(200, json={"versions": {"slurm": "25.11.2"}})
        if path == "/api/agents/4090-3/ping":
            return httpx.Response(200, json={"versions": {"slurm": "25.11.2"}})
        if path == "/api/agents/a800-1/nodes":
            return httpx.Response(200, json=[{"name": "a800-node", "state": ["ALLOCATED"]}])
        if path == "/api/agents/4090-3/nodes":
            return httpx.Response(200, json=[{"name": "4090-node", "state": ["IDLE"]}])
        if path == "/api/agents/a800-1/partitions":
            return httpx.Response(200, json=[{"name": "gpu", "state": ["UP"]}])
        if path == "/api/agents/4090-3/partitions":
            return httpx.Response(200, json=[{"name": "gpu", "state": ["UP"]}])
        if path == "/api/agents/a800-1/jobs":
            return httpx.Response(200, json=[{"job_id": 1, "user_name": "qinghanw313"}])
        if path == "/api/agents/4090-3/jobs":
            return httpx.Response(200, json=[{"job_id": 2, "user_name": "other"}])
        raise AssertionError(path)

    async def run() -> dict[str, Any]:
        client = SlurmWebGatewayClient(
            "http://slurm-web-gateway:5011",
            cluster=("a800-1", "4090-3"),
            jwt_key_file=str(key),
            transport=httpx.MockTransport(handler),
        )
        with use_tool_context(ToolContext(user={"user_id": "qinghanw313"})):
            status = await client.cluster_status(max_jobs=10, max_unhealthy_nodes=10)
            nodes = await client.nodes(max_results=10)
            partitions = await client.partitions(max_results=10)
        return {"client": client, "status": status, "nodes": nodes, "partitions": partitions}

    result = asyncio.run(run())

    assert result["client"].groups == ("cluster.a800-1.user", "cluster.4090-3.user")
    assert result["status"]["nodes"]["count"] == 2
    assert result["status"]["jobs"]["items"] == [{"job_id": 1, "user": "qinghanw313", "cluster": "a800-1"}]
    assert {node["cluster"] for node in result["nodes"]["nodes"]} == {"a800-1", "4090-3"}
    assert {partition["cluster"] for partition in result["partitions"]["partitions"]} == {"a800-1", "4090-3"}
    token = requests[0].headers["authorization"].removeprefix("Bearer ")
    payload = json.loads(_decode_segment(token.split(".")[1]))
    assert payload["groups"] == ["cluster.a800-1.user", "cluster.4090-3.user"]


def test_slurmweb_gateway_config_registers_client(tmp_path: Path) -> None:
    key = tmp_path / "jwt.key"
    key.write_text("gateway-key", encoding="utf-8")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/api/agents/a800-1/jobs"
        return httpx.Response(200, json=[{"job_id": 7, "user_name": "qinghanw313", "job_state": "RUNNING"}])

    async def run():
        register_bundled_plugins()
        registry = build_tool_registry(
            ["slurm"],
            BundleContext(
                extra={
                    "slurm": {
                        "gateway_url": "http://slurm-web-gateway:5011",
                        "cluster": "a800-1",
                        "jwt_key_file": str(key),
                        "groups": ("cluster.a800-1.user",),
                        "transport": httpx.MockTransport(handler),
                    }
                }
            ),
        )
        with use_tool_context(ToolContext(user={"user_id": "qinghanw313"})):
            return await registry.dispatch("list_slurm_jobs", {"max_results": 5})

    result = asyncio.run(run())

    assert result.outcome is ToolOutcome.COMPLETED
    assert result.content["jobs"] == [{"job_id": 7, "user": "qinghanw313", "states": ["running"], "cluster": "a800-1"}]
    token = requests[0].headers["authorization"].removeprefix("Bearer ")
    payload = json.loads(_decode_segment(token.split(".")[1]))
    assert payload["sub"] == "qinghanw313"


def _decode_segment(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
