from __future__ import annotations

import pytest

from feishu.agent.app import Agent, AgentConfigurationError


def test_validate_agent_config_rejects_non_boolean_stream_setting() -> None:
    validate = getattr(Agent, "validate_config", None)
    assert callable(validate)
    issues = validate(
        {
            "feishu": {"app_id": "cli_test", "app_secret": "secret"},
            "model": {"model": "example", "api_key": "key", "base_url": "https://api.example/v1"},
            "reply": {"stream": "false"},
        }
    )

    assert any(issue.field == "reply.stream" and issue.message == "must be a boolean" for issue in issues)


def test_validate_agent_config_accepts_injected_client_and_backend_without_provider_config() -> None:
    validate = getattr(Agent, "validate_config", None)
    assert callable(validate)
    issues = validate(
        {"toolkits": []},
        injected={"client", "backend"},
    )

    assert issues == ()


def test_agent_rejects_invalid_config_before_creating_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[str] = []

    def create_client(_self: Agent):
        created.append("client")
        raise AssertionError("invalid configuration must be rejected before client construction")

    monkeypatch.setattr(Agent, "_client_from_config", create_client)

    with pytest.raises(AgentConfigurationError) as exc_info:
        Agent(
            {
                "feishu": {"app_id": "cli_test", "app_secret": "secret"},
                "model": {"model": "example", "api_key": "key", "base_url": "https://api.example/v1"},
                "reply": {"stream": "false"},
            }
        )

    assert "reply.stream" in str(exc_info.value)
    assert created == []


def test_agent_rejects_unknown_bundle_before_creating_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[str] = []

    def create_client(_self: Agent):
        created.append("client")
        raise AssertionError("unknown bundles must be rejected before client construction")

    monkeypatch.setattr(Agent, "_client_from_config", create_client)

    with pytest.raises(AgentConfigurationError) as exc_info:
        Agent(
            {
                "feishu": {"app_id": "cli_test", "app_secret": "secret"},
                "model": {"model": "example", "api_key": "key", "base_url": "https://api.example/v1"},
                "toolkits": ["missing.bundle"],
            }
        )

    assert any(issue.field == "toolkits" for issue in exc_info.value.issues)
    assert created == []


def test_injected_registry_does_not_require_default_toolkit_names() -> None:
    issues = Agent.validate_config(
        {"toolkits": ["configured.by.application"]},
        injected={"client", "backend", "registry"},
    )

    assert issues == ()


def test_validate_agent_config_rejects_health_and_readiness_route_collision() -> None:
    issues = Agent.validate_config(
        {"server": {"health_path": "/status", "ready_path": "/status"}, "toolkits": []},
        injected={"client", "backend"},
    )

    assert any(issue.field == "server.ready_path" and "must differ" in issue.message for issue in issues)


def test_validate_agent_config_requires_positive_shutdown_timeout() -> None:
    issues = Agent.validate_config(
        {"runtime": {"shutdown_timeout_seconds": 0}},
        injected={"engine"},
    )

    assert any(issue.field == "runtime.shutdown_timeout_seconds" and "positive" in issue.message for issue in issues)


def test_validate_agent_config_rejects_fractional_message_limit() -> None:
    issues = Agent.validate_config(
        {"session": {"max_messages": 1.5}},
        injected={"engine"},
    )

    assert any(issue.field == "session.max_messages" and "integer" in issue.message for issue in issues)


def test_validate_agent_config_checks_prompt_file_before_agent_construction(tmp_path) -> None:
    missing_prompt = tmp_path / "missing-system.md"
    issues = Agent.validate_config(
        {"system_path": str(missing_prompt)},
        injected={"engine"},
    )

    assert any(issue.field == "system_path" and "readable" in issue.message for issue in issues)


@pytest.mark.parametrize(
    ("config", "field"),
    [
        ({"feishu": {"region": "unknown"}}, "feishu.region"),
        ({"server": {"seen_store": "redis"}}, "server.seen_store"),
    ],
)
def test_validate_agent_config_rejects_invalid_runtime_choices(config, field) -> None:
    issues = Agent.validate_config(config, injected={"engine"})

    assert any(issue.field == field for issue in issues)


def test_unused_prompt_path_does_not_fail_when_prompt_text_is_supplied(tmp_path) -> None:
    issues = Agent.validate_config(
        {"system": "You are helpful.", "system_path": str(tmp_path / "unused.md")},
        injected={"engine"},
    )

    assert not any(issue.field == "system_path" for issue in issues)


def test_validate_agent_config_requires_storage_for_memory_toolkit() -> None:
    issues = Agent.validate_config(
        {"toolkits": ["memory"], "memory": {"enabled": False}},
        injected={"engine"},
    )

    assert any(issue.field == "toolkits" and "memory.enabled" in issue.message for issue in issues)


def test_validate_agent_config_checks_fast_model_used_with_custom_backend() -> None:
    issues = Agent.validate_config(
        {"fast_model": {"model": "fast-model"}},
        injected={"client", "backend"},
    )

    assert any(issue.field == "fast_model.api_key" for issue in issues)
    assert any(issue.field == "fast_model.base_url" for issue in issues)
