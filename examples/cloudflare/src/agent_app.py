"""Build OpenFeishu ASGI apps for the Cloudflare Worker runtime and local smoke tests."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from typing import Any

from feishu.agent import Agent, ToolRegistry
from feishu.agent.approval import DefaultApprovalEngine
from feishu.agent.llm import MessageStop, StopReason, TextDelta
from feishu.agent.loop import AgentEngine, session_id_for
from feishu.agent.persistence import (
    SqliteExecutionResultStore,
    SqliteMemoryStore,
    SqlitePendingApprovalStore,
    SqlitePendingAuthorizationStore,
    SqliteSessionStore,
)
from feishu.agent.shared_files import SqliteSharedFileStore
from feishu.auth import SqliteOAuthTokenStore, UserTokenProvider
from feishu.client import FeishuClient

_storage = import_module(f"{__package__}.storage" if __package__ else "storage")
CloudflareAuditLog = _storage.CloudflareAuditLog
CloudflareEventJobStore = _storage.CloudflareEventJobStore
CloudflareSeenStore = _storage.CloudflareSeenStore
CloudflareSqliteConnection = _storage.CloudflareSqliteConnection


class _UnconfiguredBackend:
    """Keep the probe explicit: message processing needs a configured model backend."""

    def stream(self, **_kwargs: Any) -> Any:
        raise RuntimeError("configure a Cloudflare-compatible model backend before handling messages")


def create_app() -> Any:
    """Create an in-memory app that exercises the shared Starlette/ASGI routes."""
    engine = AgentEngine(backend=_UnconfiguredBackend(), registry=ToolRegistry())
    agent = Agent(
        {
            "feishu": {"encrypt_key": "cf-encrypt", "verification_token": "cf-smoke"},
            "server": {"seen_store": "none"},
        },
        engine=engine,
    )
    return agent.asgi_app()


def is_local_smoke(env: Any) -> bool:
    return _binding(env, "AGENT_LOCAL_SMOKE", "").strip().lower() in {"1", "true", "yes"}


def create_runtime_agent(
    env: Any,
    *,
    connection: Any,
    jobs: Any,
    seen_store: Any,
) -> Agent:
    """Assemble a persistent Agent against the Durable Object database."""
    config = _config(env)
    client: Any
    sessions = SqliteSessionStore(":memory:", connection=connection)
    approvals = SqlitePendingApprovalStore(":memory:", connection=connection)
    authorizations = SqlitePendingAuthorizationStore(":memory:", connection=connection)
    executions = SqliteExecutionResultStore(":memory:", connection=connection)
    shared_files = SqliteSharedFileStore(":memory:", connection=connection)
    memory = SqliteMemoryStore(":memory:", connection=connection) if config["memory"]["enabled"] else None
    approval_engine = DefaultApprovalEngine(
        approvals=approvals,
        executions=executions,
        audit=CloudflareAuditLog(connection),
    )

    if is_local_smoke(env):
        client = _SmokeClient()
        engine = AgentEngine(
            backend=_SmokeBackend(),
            registry=ToolRegistry(),
            store=sessions,
            client=client,
            approvals=approvals,
            authorizations=authorizations,
            approval_engine=approval_engine,
            shared_files_store=shared_files,
            memory_store=memory,
        )
        return Agent(
            config,
            engine=engine,
            client=client,
            seen_store=seen_store,
            event_job_store=jobs,
            event_partition_key=session_id_for,
        )

    client = FeishuClient(
        config["feishu"]["app_id"],
        config["feishu"]["app_secret"],
        region=config["feishu"]["region"],
    )
    token_provider = UserTokenProvider(client, SqliteOAuthTokenStore(":memory:", connection=connection))
    return Agent(
        config,
        client=client,
        user_tokens=token_provider,
        store=sessions,
        approvals=approvals,
        authorizations=authorizations,
        approval_engine=approval_engine,
        shared_files_store=shared_files,
        memory_store=memory,
        seen_store=seen_store,
        event_job_store=jobs,
        event_partition_key=session_id_for,
    )


def _config(env: Any) -> dict[str, Any]:
    smoke = is_local_smoke(env)
    app_id = _required(env, "FEISHU_APP_ID", smoke=smoke)
    app_secret = _required(env, "FEISHU_APP_SECRET", smoke=smoke)
    api_key = _required(env, "OPENAI_API_KEY", smoke=smoke)
    model = _required(env, "OPENAI_MODEL", smoke=smoke)
    toolkits = [value.strip() for value in _binding(env, "AGENT_TOOLKITS", "").split(",") if value.strip()]
    return {
        "feishu": {
            "app_id": app_id,
            "app_secret": app_secret,
            "encrypt_key": _binding(env, "FEISHU_ENCRYPT_KEY", "") or None,
            "verification_token": _binding(env, "FEISHU_VERIFICATION_TOKEN", "") or None,
            "region": _binding(env, "FEISHU_REGION", "feishu"),
        },
        "model": {
            "model": model,
            "api_key": api_key,
            "base_url": _binding(env, "OPENAI_BASE_URL", "https://api.openai.com/v1"),
        },
        "oauth": {
            "public_url": _binding(env, "AGENT_PUBLIC_URL", "") or None,
            "state_secret": _binding(env, "AGENT_OAUTH_STATE_SECRET", "") or None,
        },
        "storage": {"path": ":memory:"},
        "server": {"seen_store": "none", "event_path": "/feishu/event", "card_path": "/feishu/card"},
        "session": {"max_messages": 400},
        "reply": {"stream": False},
        "memory": {
            "enabled": _binding(env, "AGENT_MEMORY_ENABLED", "false").lower() in {"1", "true", "yes"},
        },
        "runtime": {"shutdown_timeout_seconds": 10},
        "toolkits": toolkits,
        "system": _binding(env, "AGENT_SYSTEM_PROMPT", "你是一个简洁的飞书助手。必要时使用工具。"),
        "timezone": _binding(env, "AGENT_TIMEZONE", "Asia/Shanghai"),
    }


def _binding(env: Any, name: str, default: str = "") -> str:
    if isinstance(env, Mapping):
        value = env.get(name, default)
    else:
        value = getattr(env, name, default)
    return default if value is None else str(value)


def _required(env: Any, name: str, *, smoke: bool) -> str:
    value = _binding(env, name, "")
    if value:
        return value
    if smoke:
        return f"local-{name.lower()}"
    raise RuntimeError(f"{name} Worker secret is required")


class _SmokeClient:
    def __init__(self) -> None:
        self.im = _SmokeIM()


class _SmokeIM:
    async def reply(self, _message_id: str, _text: str, *, msg_type: str) -> dict[str, Any]:
        return {"message_id": "om-local-smoke", "msg_type": msg_type}


class _SmokeBackend:
    def stream(self, *, messages, **_kwargs):
        latest = next((message for message in reversed(messages) if message.role == "user"), None)
        text = "local smoke passed"
        if latest is not None:
            text = "local smoke passed: " + "".join(part.text for part in latest.content if hasattr(part, "text"))

        async def response():
            yield TextDelta(text=text)
            yield MessageStop(stop_reason=StopReason.END_TURN)

        return response()
