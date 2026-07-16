from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import httpx

from feishu.agent.bundles import BundleContext, build_tool_registry
from feishu.agent.context import ToolContext, use_tool_context
from feishu.agent.result import ToolOutcome
from feishu.integrations import MLflowClient, SlurmRestdClient, register_bundled_integrations


def test_registry() -> None:
    names = register_bundled_integrations()

    assert names == ("mlflow", "ops", "slurm")
    registry = build_tool_registry(names, BundleContext())

    assert registry.get("get_operational_health").name == "get_operational_health"
    assert registry.get("normalize_mlflow_run").name == "normalize_mlflow_run"
    assert registry.get("search_mlflow_experiments").name == "search_mlflow_experiments"
    assert registry.get("search_mlflow_runs").name == "search_mlflow_runs"
    assert registry.get("get_mlflow_run").name == "get_mlflow_run"
    assert registry.get("get_slurm_cluster_status").name == "get_slurm_cluster_status"
    assert registry.get("list_slurm_nodes").name == "list_slurm_nodes"
    assert registry.get("list_slurm_jobs").name == "list_slurm_jobs"
    assert registry.get("list_slurm_partitions").name == "list_slurm_partitions"


def test_slurm_tools() -> None:
    class _SlurmClient:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        async def cluster_status(self, *, max_jobs: int = 20, max_unhealthy_nodes: int = 20):
            self.calls.append(("cluster_status", max_jobs, max_unhealthy_nodes))
            return {"nodes": {"count": 2}, "jobs": {"count": 1}}

        async def nodes(self, *, state: str | None = None, max_results: int = 100):
            self.calls.append(("nodes", state, max_results))
            return {"nodes": [{"name": "gpu-1", "states": ["down"]}]}

        async def jobs(self, *, state: str | None = None, user: str | None = None, max_results: int = 50):
            self.calls.append(("jobs", state, user, max_results))
            return {"jobs": [{"job_id": 42, "user": user, "states": [state]}]}

        async def partitions(self, *, max_results: int = 50):
            self.calls.append(("partitions", max_results))
            return {"partitions": [{"name": "gpu"}]}

    async def run():
        client = _SlurmClient()
        register_bundled_integrations()
        registry = build_tool_registry(
            ["slurm"],
            BundleContext(
                extra={
                    "slurm": {"client": client},
                }
            ),
        )
        with use_tool_context(ToolContext(user={"user_id": "alice"})):
            status = await registry.dispatch("get_slurm_cluster_status", {"max_jobs": 5, "max_unhealthy_nodes": 2})
            nodes = await registry.dispatch("list_slurm_nodes", {"state": "down", "max_results": 1})
            jobs = await registry.dispatch("list_slurm_jobs", {"state": "running", "user": "alice", "max_results": 1})
            partitions = await registry.dispatch("list_slurm_partitions", {"max_results": 1})
        return client, status, nodes, jobs, partitions

    client, status, nodes, jobs, partitions = asyncio.run(run())

    assert status.outcome is ToolOutcome.COMPLETED
    assert status.content["nodes"]["count"] == 2
    assert nodes.content["nodes"][0]["name"] == "gpu-1"
    assert jobs.content["jobs"][0]["user"] == "alice"
    assert partitions.content["partitions"][0]["name"] == "gpu"
    assert client.calls == [
        ("cluster_status", 5, 2),
        ("nodes", "down", 1),
        ("jobs", "running", "alice", 1),
        ("partitions", 1),
    ]


def test_slurm_tools_resolve_open_id_requester() -> None:
    class _Users:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def get(self, user_id, *, user_id_type="open_id", **kwargs):
            self.calls.append((user_id, user_id_type))
            return {"user": {"user_id": "qinghanw313"}}

    class _Contact:
        def __init__(self) -> None:
            self.users = _Users()

    class _FeishuClient:
        def __init__(self) -> None:
            self.contact = _Contact()

    class _SlurmClient:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        async def cluster_status(self, *, max_jobs: int = 20, max_unhealthy_nodes: int = 20):
            self.calls.append(("cluster_status", max_jobs, max_unhealthy_nodes))
            return {"jobs": {"items": []}, "nodes": {"unhealthy": []}}

    async def run():
        feishu_client = _FeishuClient()
        slurm_client = _SlurmClient()
        register_bundled_integrations()
        registry = build_tool_registry(["slurm"], BundleContext(extra={"slurm": {"client": slurm_client}}))
        with use_tool_context(ToolContext(client=feishu_client, user={"open_id": "ou_qing"})):
            result = await registry.dispatch("get_slurm_cluster_status", {})
        return feishu_client, slurm_client, result

    feishu_client, slurm_client, result = asyncio.run(run())

    assert result.outcome is ToolOutcome.COMPLETED
    assert feishu_client.contact.users.calls == [("ou_qing", "open_id")]
    assert slurm_client.calls == [("cluster_status", 20, 20)]


def test_slurm_tools_bind_injected_client_user() -> None:
    class _BoundSlurmClient:
        def __init__(self, username: str) -> None:
            self.username = username
            self.calls: list[tuple[Any, ...]] = []

        async def cluster_status(self, *, max_jobs: int = 20, max_unhealthy_nodes: int = 20):
            self.calls.append(("cluster_status", self.username, max_jobs, max_unhealthy_nodes))
            return {"nodes": {"unhealthy": []}, "jobs": {"items": []}}

    class _SlurmClient:
        def __init__(self) -> None:
            self.bound: list[_BoundSlurmClient] = []

        def with_user(self, username: str) -> _BoundSlurmClient:
            bound = _BoundSlurmClient(username)
            self.bound.append(bound)
            return bound

    async def run():
        client = _SlurmClient()
        register_bundled_integrations()
        registry = build_tool_registry(["slurm"], BundleContext(extra={"slurm": {"client": client}}))
        with use_tool_context(ToolContext(user={"user_id": "qinghanw313"})):
            result = await registry.dispatch("get_slurm_cluster_status", {})
        return client, result

    client, result = asyncio.run(run())

    assert result.outcome is ToolOutcome.COMPLETED
    assert len(client.bound) == 1
    assert client.bound[0].calls == [("cluster_status", "qinghanw313", 20, 20)]


def test_slurm_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-slurm-user-name"] == "loki"
        assert request.headers["x-slurm-user-token"] == "jwt-token"
        if request.url.path == "/slurm/v0.0.45/ping/":
            return httpx.Response(200, json={"pings": [{"hostname": "ctrl", "pinged": "UP"}]})
        if request.url.path == "/slurm/v0.0.45/nodes/":
            return httpx.Response(
                200,
                json={
                    "nodes": [
                        {
                            "name": "gpu-1",
                            "state": ["ALLOCATED"],
                            "partitions": ["gpu"],
                            "cpus": 128,
                            "alloc_cpus": 64,
                        },
                        {"name": "gpu-2", "state": "DOWN+DRAIN", "reason": "hardware"},
                    ]
                },
            )
        if request.url.path == "/slurm/v0.0.45/partitions/":
            return httpx.Response(200, json={"partitions": [{"name": "gpu", "state": ["UP"], "total_nodes": 2}]})
        if request.url.path == "/slurm/v0.0.45/jobs/":
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "job_id": 42,
                            "name": "train",
                            "user_name": "alice",
                            "job_state": ["RUNNING"],
                            "partition": "gpu",
                            "nodes": "gpu-1",
                        },
                        {"job_id": 43, "user_name": "bob", "job_state": ["PENDING"], "state_reason": "Priority"},
                    ]
                },
            )
        raise AssertionError(request.url.path)

    async def run():
        client = SlurmRestdClient(
            "https://slurm.example",
            user="loki",
            token="jwt-token",
            transport=httpx.MockTransport(handler),
        )
        status = await client.cluster_status(max_jobs=1, max_unhealthy_nodes=1)
        down_nodes = await client.nodes(state="down")
        alice_jobs = await client.jobs(state="running", user="alice")
        partitions = await client.partitions()
        return status, down_nodes, alice_jobs, partitions

    status, down_nodes, alice_jobs, partitions = asyncio.run(run())

    assert status["nodes"]["count"] == 2
    assert status["nodes"]["by_state"] == {"allocated": 1, "down": 1, "drain": 1}
    assert status["nodes"]["unhealthy"] == [{"name": "gpu-2", "states": ["down", "drain"], "reason": "hardware"}]
    assert status["jobs"]["count"] == 2
    assert status["jobs"]["items"] == [
        {
            "job_id": 42,
            "name": "train",
            "user": "alice",
            "partition": "gpu",
            "states": ["running"],
            "nodes": "gpu-1",
        }
    ]
    assert down_nodes["nodes"] == [{"name": "gpu-2", "states": ["down", "drain"], "reason": "hardware"}]
    assert alice_jobs["jobs"][0]["job_id"] == 42
    assert partitions["partitions"] == [{"name": "gpu", "states": ["up"], "total_nodes": 2}]


def test_ops_summary() -> None:
    class _SlurmClient:
        async def cluster_status(self, *, max_jobs: int = 20, max_unhealthy_nodes: int = 20):
            return {"nodes": {"unhealthy": [{"name": "gpu-2"}]}, "jobs": {"items": []}}

    async def run():
        register_bundled_integrations()
        registry = build_tool_registry(
            ["ops"],
            BundleContext(
                extra={
                    "ops": {},
                    "slurm": {"client": _SlurmClient()},
                }
            ),
        )
        with use_tool_context(ToolContext(user={"user_id": "u_ops"})):
            return await registry.dispatch(
                "get_operational_health",
                {"max_jobs": 3, "max_unhealthy_nodes": 2},
            )

    result = asyncio.run(run())

    assert result.outcome is ToolOutcome.COMPLETED
    assert result.content["status"] == "degraded"
    assert result.content["issues"] == [
        {"source": "slurm", "kind": "unhealthy_nodes", "count": 1},
    ]


def test_run_normalizer() -> None:
    register_bundled_integrations()
    registry = build_tool_registry(["mlflow"], BundleContext())
    result = registry.get("normalize_mlflow_run").handler(
        {
            "run": {
                "info": {
                    "run_id": "run_1",
                    "experiment_id": "exp_1",
                    "status": "FAILED",
                    "artifact_uri": "s3://bucket/run_1",
                },
                "data": {
                    "metrics": {"loss": 0.42},
                    "params": {"lr": "1e-4"},
                    "tags": {"mlflow.runName": "trial-1"},
                },
            }
        }
    )

    assert isinstance(result, dict)
    assert result["run_id"] == "run_1"
    assert result["experiment_id"] == "exp_1"
    assert result["status"] == "FAILED"
    assert result["name"] == "trial-1"
    assert result["metrics"] == {"loss": 0.42}


def test_mlflow_tools() -> None:
    class _MLflowClient:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        async def search_experiments(self, *, query: str | None = None, max_results: int = 20):
            self.calls.append(("search_experiments", query, max_results))
            return [{"experiment_id": "12", "name": "train"}]

        async def search_runs(
            self,
            *,
            experiment_ids=None,
            experiment_name: str | None = None,
            filter: str | None = None,
            max_results: int = 10,
            order_by=None,
        ):
            self.calls.append(("search_runs", experiment_ids, experiment_name, filter, max_results, order_by))
            return [
                {
                    "run_id": "run_1",
                    "experiment_id": "12",
                    "status": "RUNNING",
                    "name": "trial-1",
                    "tags": {"mlflow.user": "u_ops"},
                }
            ]

        async def get_run(self, run_id: str):
            self.calls.append(("get_run", run_id))
            return {"run_id": run_id, "status": "RUNNING", "metrics": {"loss": 0.2}, "tags": {"mlflow.user": "u_ops"}}

    async def run():
        client = _MLflowClient()
        register_bundled_integrations()
        registry = build_tool_registry(
            ["mlflow"],
            BundleContext(extra={"mlflow": {"client": client}}),
        )
        with use_tool_context(ToolContext(user={"user_id": "u_ops"})):
            experiments = await registry.dispatch("search_mlflow_experiments", {"query": "train", "max_results": 3})
            runs = await registry.dispatch(
                "search_mlflow_runs",
                {
                    "experiment_ids": ["12"],
                    "filter": "attributes.status = 'RUNNING'",
                    "max_results": 5,
                    "order_by": ["attributes.start_time DESC"],
                },
            )
            run_detail = await registry.dispatch("get_mlflow_run", {"run_id": "run_1"})
        return client, experiments, runs, run_detail

    client, experiments, runs, run_detail = asyncio.run(run())

    assert experiments.outcome is ToolOutcome.COMPLETED
    assert experiments.content["experiments"][0]["name"] == "train"
    assert runs.content["runs"][0]["status"] == "RUNNING"
    assert run_detail.content["run"]["metrics"] == {"loss": 0.2}
    assert client.calls == [
        ("search_experiments", "train", 3),
        ("search_runs", ["12"], None, None, 100, None),
        ("search_runs", ["12"], None, "attributes.status = 'RUNNING'", 5, ["attributes.start_time DESC"]),
        ("get_run", "run_1"),
    ]


def test_tracking_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        expected = base64.b64encode(b"loki@example.com:mlflow-token").decode()
        assert request.headers["authorization"] == f"Basic {expected}"
        if request.url.path == "/api/2.0/mlflow/experiments/search":
            body = json.loads(request.content)
            assert body["view_type"] == "ALL"
            return httpx.Response(200, json={"experiments": [{"experiment_id": "12", "name": "train"}]})
        if request.url.path == "/api/2.0/mlflow/runs/search":
            body = json.loads(request.content)
            assert body["experiment_ids"] == ["12"]
            return httpx.Response(
                200,
                json={
                    "runs": [
                        {
                            "info": {"run_id": "run_1", "experiment_id": "12", "status": "RUNNING"},
                            "data": {
                                "metrics": [{"key": "loss", "value": 0.2}],
                                "params": [{"key": "lr", "value": "1e-4"}],
                                "tags": [{"key": "mlflow.runName", "value": "trial-1"}],
                            },
                        }
                    ]
                },
            )
        if request.url.path == "/api/2.0/mlflow/runs/get":
            assert request.url.params["run_id"] == "run_1"
            return httpx.Response(
                200,
                json={"run": {"info": {"run_id": "run_1", "status": "FINISHED"}, "data": {"metrics": []}}},
            )
        raise AssertionError(request.url.path)

    async def run():
        client = MLflowClient(
            "https://tracker.example",
            username="loki@example.com",
            api_token="mlflow-token",
            transport=httpx.MockTransport(handler),
        )
        experiments = await client.search_experiments(query="train")
        runs = await client.search_runs(experiment_ids=["12"])
        run_detail = await client.get_run("run_1")
        return experiments, runs, run_detail

    experiments, runs, run_detail = asyncio.run(run())

    assert experiments == [{"experiment_id": "12", "name": "train", "lifecycle_stage": "", "artifact_location": ""}]
    assert runs == [
        {
            "run_id": "run_1",
            "experiment_id": "12",
            "status": "RUNNING",
            "name": "trial-1",
            "artifact_uri": "",
            "metrics": {"loss": 0.2},
            "params": {"lr": "1e-4"},
            "tags": {"mlflow.runName": "trial-1"},
        }
    ]
    assert run_detail["status"] == "FINISHED"
