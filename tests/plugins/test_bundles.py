from __future__ import annotations

from feishu.agent.bundles import BundleContext, build_tool_registry
from feishu.plugins import register_bundled_plugins


def test_bundled_plugins_are_registered_explicitly() -> None:
    names = register_bundled_plugins()

    assert names == ("grafana", "mlflow")
    registry = build_tool_registry(names, BundleContext())

    assert registry.get("normalize_grafana_alerts").name == "normalize_grafana_alerts"
    assert registry.get("normalize_mlflow_run").name == "normalize_mlflow_run"


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


def test_mlflow_plugin_registers_run_normalizer_tool() -> None:
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
