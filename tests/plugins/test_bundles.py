from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import httpx

from feishu.agent.bundles import BundleContext, build_tool_registry
from feishu.agent.context import ToolContext, use_tool_context
from feishu.agent.result import ToolOutcome
from feishu.plugins import MLflowClient, register_bundled_plugins


def test_bundled_plugins_are_registered_explicitly() -> None:
    names = register_bundled_plugins()

    assert names == ("grafana", "mlflow")
    registry = build_tool_registry(names, BundleContext())

    assert registry.get("normalize_grafana_alerts").name == "normalize_grafana_alerts"
    assert registry.get("normalize_mlflow_run").name == "normalize_mlflow_run"
    assert registry.get("search_mlflow_experiments").name == "search_mlflow_experiments"
    assert registry.get("search_mlflow_runs").name == "search_mlflow_runs"
    assert registry.get("get_mlflow_run").name == "get_mlflow_run"


def test_grafana_plugin_registers_alert_normalizer_tool() -> None:
    register_bundled_plugins()
    registry = build_tool_registry(["grafana"], BundleContext())
    result = registry.get("normalize_grafana_alerts").handler(
        {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "HighGPUError", "cluster": "a800-1"},
                    "annotations": {"summary": "GPU node unhealthy"},
                    "generatorURL": "https://grafana.example/alert/1",
                }
            ],
        }
    )

    assert isinstance(result, dict)
    assert result["status"] == "firing"
    assert result["alert_count"] == 1
    assert result["alerts"][0]["title"] == "HighGPUError"
    assert result["alerts"][0]["cluster"] == "a800-1"
    assert result["alerts"][0]["url"] == "https://grafana.example/alert/1"


def test_run_normalizer() -> None:
    register_bundled_plugins()
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
        register_bundled_plugins()
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
