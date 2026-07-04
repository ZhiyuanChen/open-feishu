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

r"""开箱即用的 Feishu agent 应用入口：`Agent(config)`。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import signal
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

from ..attachments import SandboxedAttachmentExtractor, analyze_attachment
from ..auth import (
    OAuthStateSigner,
    SqliteOAuthTokenStore,
    UserTokenProvider,
    build_oauth_redirect_uri,
    normalize_oauth_callback_path,
)
from ..client import FeishuClient
from ..events.receiver import create_event_route
from ..ws.client import WsClient
from .adapters.openai import OpenAIBackend
from .approval import DefaultApprovalEngine
from .bundles import BundleContext, build_tool_registry
from .loop import AgentEngine
from .oauth import build_authorize_url_builder, oauth_callback_handler
from .payment_accounts import PaymentAccountResolver
from .persistence import (
    JsonlAuditLog,
    SqliteExecutionResultStore,
    SqlitePendingApprovalStore,
    SqlitePendingAuthorizationStore,
    SqliteSessionStore,
)
from .progress import build_progress_summarizer
from .prompting import build_time_aware_system_prompt, build_timezone_resolver
from .registration import create_agent_dispatcher
from .shared_files import SharedFileResolver, SqliteSharedFileStore
from .summarization import build_fast_text_summarizer
from .tools import ToolRegistry

logger = logging.getLogger("feishu")


class Agent:
    r"""
    基于配置开箱即用地装配并运行 Feishu agent。

    `Agent(config)` 只接收已经加载好的 mapping / config object，不负责读取 yaml、toml 或环境变量。产品层可以用
    `chanfig.load(...)`、环境变量或自己的配置系统得到配置后传入。

    Examples:
        >>> config = {"model": {"model": "gpt-4o", "api_key": "k", "base_url": "https://api.example/v1"}}
        >>> agent = Agent(config)  # doctest:+SKIP
        >>> agent.run(backend="ws")  # doctest:+SKIP
    """

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        engine: AgentEngine | None = None,
        client: Any | None = None,
        model_client: Any | None = None,
        backend: Any | None = None,
        registry: ToolRegistry | None = None,
        user_tokens: Any | None = None,
        authorize_url_builder: Any | None = None,
        describe_analyzer: Any | None = None,
        progress_summarizer: Any | None = None,
        text_summarizer: Any | None = None,
        **engine_overrides: Any,
    ) -> None:
        self.config = config or {}
        if engine is not None:
            self.client = client
            self.model_client = model_client
            self.backend = backend
            self.progress_summarizer = progress_summarizer
            self.text_summarizer = text_summarizer
            self.extractor = SandboxedAttachmentExtractor()
            self.provider = user_tokens
            self.signer = None
            self.oauth_callback_path = normalize_oauth_callback_path(
                self._get("oauth.callback_path", "/oauth/callback")
            )
            self.oauth_redirect_uri = None
            self.authorize_url_builder = authorize_url_builder
            self.describe_analyzer = describe_analyzer
            self.engine = engine
            return
        self.client = client if client is not None else self._client_from_config()
        self.model_client = model_client
        self.backend = backend
        self.progress_summarizer = progress_summarizer
        self.text_summarizer = text_summarizer
        self.extractor = SandboxedAttachmentExtractor()
        self.provider = user_tokens or UserTokenProvider(
            self.client,
            SqliteOAuthTokenStore(self.db_path),
        )
        self.signer = self._oauth_state_signer()
        self.oauth_callback_path = normalize_oauth_callback_path(self._get("oauth.callback_path", "/oauth/callback"))
        self.oauth_redirect_uri = build_oauth_redirect_uri(self._get("oauth.public_url"), self.oauth_callback_path)
        self.authorize_url_builder = authorize_url_builder or self._authorize_url_builder()
        self.describe_analyzer = describe_analyzer
        if engine is not None:
            self.engine = engine
            return
        if self.backend is None and self.model_client is None:
            self.model_client = self._openai_client_from_config()
        if self.backend is None:
            self.backend = self._model_backend_from_config(self.model_client)
        if self.progress_summarizer is None:
            self.progress_summarizer = self._progress_summarizer_from_config()
        if self.text_summarizer is None:
            self.text_summarizer = self._text_summarizer_from_config()
        if self.describe_analyzer is None and self.model_client is not None:
            self.describe_analyzer = self._describe_analyzer()
        self.engine = self._engine(registry=registry, **engine_overrides)

    @property
    def db_path(self) -> str:
        storage = self._section("storage")
        path = storage.get("path") or storage.get("db_path") or ".agent/agent.db"
        return str(path)

    @property
    def audit_path(self) -> str:
        storage = self._section("storage")
        return str(storage.get("audit_path") or str(Path(self.db_path).with_name("audit.jsonl")))

    def run(self, *, backend: Literal["ws", "http"] = "ws") -> None:
        r"""启动 agent。`backend="ws"` 运行飞书长连接，`backend="http"` 运行 HTTP webhook 服务。"""
        if backend == "ws":
            asyncio.run(self.run_ws())
            return
        if backend == "http":
            self.run_http()
            return
        raise ValueError(f"unknown agent run backend: {backend!r}")

    async def run_ws(self) -> None:
        r"""运行飞书 WebSocket 长连接服务。"""
        feishu = self._section("feishu")
        app_id = str(feishu.get("app_id") or "")
        app_secret = str(feishu.get("app_secret") or "")
        if not (app_id and app_secret):
            raise RuntimeError("feishu.app_id / feishu.app_secret are required for ws backend")
        if self.signer is not None and self.oauth_redirect_uri:
            logger.warning(
                "ws backend does not serve OAuth callback; run http backend to handle %s", self.oauth_callback_path
            )
        ws = WsClient(app_id, app_secret, self.dispatcher(), region=str(feishu.get("region") or "feishu"))
        loop = asyncio.get_running_loop()

        def stop() -> None:
            logger.info("shutdown signal received; closing the ws connection gracefully")
            asyncio.ensure_future(ws.aclose())

        for sig in (signal.SIGTERM, signal.SIGINT):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop)
        try:
            await ws.start()
        finally:
            await ws.aclose()

    def run_http(self) -> None:
        r"""运行 HTTP webhook 服务。"""
        import uvicorn

        server = self._section("server")
        uvicorn.run(
            self.asgi_app(),
            host=str(server.get("host") or "127.0.0.1"),
            port=int(server.get("port") or 5654),
            log_level=str(server.get("log_level") or "info").lower(),
        )

    def asgi_app(self) -> Any:
        r"""返回可交给 ASGI server 的 Starlette app。"""
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        server = self._section("server")

        async def health(_request: Any) -> Any:
            return JSONResponse({"ok": True, "service": str(server.get("service") or "feishu-agent")})

        routes = [
            Route(str(server.get("health_path") or "/health"), health, methods=["GET"]),
            create_event_route(
                self.dispatcher(),
                path=str(server.get("event_path") or "/feishu/event"),
                encrypt_key=self._get("feishu.encrypt_key"),
                verification_token=self._get("feishu.verification_token"),
            ),
        ]
        routes.extend(self.extra_routes())
        if self.signer is not None and self.oauth_redirect_uri and self.provider is not None:
            handler = oauth_callback_handler(
                self.provider,
                self.signer,
                self.oauth_redirect_uri,
                on_authorized=self.engine.resume_authorization,
                success_message=self.oauth_success_message(),
                success_title=self.oauth_success_title(),
            )
            routes.append(Route(self.oauth_callback_path, handler, methods=["GET"]))
        return Starlette(routes=routes)

    def extra_routes(self) -> list[Any]:
        r"""返回产品层追加的 Starlette routes。"""
        return []

    def oauth_success_message(self) -> str:
        r"""OAuth 成功页正文。"""
        return str(self._get("oauth.success_message", "授权成功，正在回到飞书继续处理。"))

    def oauth_success_title(self) -> str:
        r"""OAuth 成功页标题。"""
        return str(self._get("oauth.success_title", "授权完成"))

    def dispatcher(self) -> Any:
        r"""创建并返回绑定了当前 engine 的事件 dispatcher。"""
        return create_agent_dispatcher(self.engine, seen_store=self._seen_store_config())

    def _seen_store_config(self) -> Any | None:
        mode_value = self._get("server.seen_store")
        if mode_value is None:
            return None
        mode = str(mode_value).strip().lower()
        if mode in {"", "none", "off", "false", "0", "disabled"}:
            return None
        if mode == "sqlite":
            from ..events.idempotency import SqliteSeenStore

            path = self._get("server.seen_db_path") or self.db_path
            ttl = float(self._get("server.seen_ttl_seconds", 7 * 24 * 3600) or 7 * 24 * 3600)
            return SqliteSeenStore(str(path), ttl=ttl)
        raise ValueError(f"unknown server.seen_store mode: {mode_value!r}")

    async def handle_event(self, event: Any) -> None:
        r"""直接处理一条消息事件。"""
        await self.engine.run(event)

    async def handle_card_action(self, event: Any) -> dict[str, Any]:
        r"""直接处理一条卡片回调事件。"""
        return await self.engine.handle_card_action(event)

    async def resume_authorization(self, authorization_id: str, *, user: Mapping[str, Any] | None = None) -> str:
        r"""在 OAuth 回调保存用户 token 后恢复挂起授权。"""
        return await self.engine.resume_authorization(authorization_id, user=user)

    def _engine(self, *, registry: ToolRegistry | None = None, **overrides: Any) -> AgentEngine:
        approvals = overrides.pop("approvals", None) or SqlitePendingApprovalStore(self.db_path)
        authorizations = overrides.pop("authorizations", None) or SqlitePendingAuthorizationStore(
            self.db_path,
            ttl_seconds=int(self._get("oauth.authorization_ttl_seconds", 3600) or 3600),
        )
        shared_files_store = overrides.pop("shared_files_store", None) or SqliteSharedFileStore(self.db_path)
        shared_files = overrides.pop("shared_files", None) or SharedFileResolver(
            shared_files_store,
            self.client,
            max_materialize_bytes=int(self._get("shared_files.max_bytes", 20 * 1024 * 1024) or 20 * 1024 * 1024),
        )
        timezone_resolver = overrides.pop("timezone", None) or self._timezone_resolver()
        if self.backend is None:
            raise RuntimeError("agent backend is not configured")
        return AgentEngine(
            backend=self.backend,
            registry=registry or self._registry(),
            client=self.client,
            store=overrides.pop(
                "store",
                SqliteSessionStore(self.db_path, max_messages=int(self._get("session.max_messages", 0) or 0)),
            ),
            approvals=approvals,
            authorizations=authorizations,
            approval_engine=overrides.pop(
                "approval_engine",
                DefaultApprovalEngine(
                    approvals=approvals,
                    executions=SqliteExecutionResultStore(self.db_path),
                    audit=JsonlAuditLog(self.audit_path),
                    outcome_status=overrides.pop("outcome_status", None),
                    idempotency_namespace=str(self._get("approval.idempotency_namespace", "agent") or "agent"),
                ),
            ),
            progress_summarizer=self.progress_summarizer,
            user_tokens=self.provider,
            authorize_url_builder=self.authorize_url_builder,
            shared_files=shared_files,
            shared_files_store=shared_files_store,
            shared_file_ttl_seconds=int(self._get("shared_files.ttl_seconds", 7 * 24 * 3600) or 7 * 24 * 3600),
            payment_accounts=overrides.pop("payment_accounts", None) or PaymentAccountResolver(self.client),
            system=overrides.pop("system", None) or self._system_prompt(timezone_resolver),
            timezone=timezone_resolver,
            summarize_threshold_tokens=int(self._get("session.summarize_threshold_tokens", 0) or 0),
            summarize_keep_recent=int(self._get("session.summarize_keep_recent", 12) or 12),
            stream=bool(self._get("reply.stream", True)),
            **overrides,
        )

    def _registry(self) -> ToolRegistry:
        toolkits = _string_tuple(self._get("toolkits", ("feishu.workplace",)))
        bundle = self._section("bundle")
        context = BundleContext(
            locale=str(self._get("locale", "zh-CN") or "zh-CN"),
            timezone=str(self._get("timezone", "Asia/Shanghai") or "Asia/Shanghai"),
            describe_analyzer=self.describe_analyzer,
            text_summarizer=self.text_summarizer,
            mail_summary_max_messages=int(bundle.get("mail_summary_max_messages") or 10),
            mail_summary_max_body_chars=int(bundle.get("mail_summary_max_body_chars") or 4000),
            mail_summary_max_chars=int(bundle.get("mail_summary_max_chars") or 2000),
            extra=dict(bundle.get("extra") or {}),
        )
        return build_tool_registry(toolkits, context)

    def _client_from_config(self) -> FeishuClient:
        feishu = self._section("feishu")
        app_id = str(feishu.get("app_id") or "")
        app_secret = str(feishu.get("app_secret") or "")
        if not (app_id and app_secret):
            raise RuntimeError("feishu.app_id / feishu.app_secret are required")
        return FeishuClient(app_id, app_secret, region=str(feishu.get("region") or "feishu"))

    def _openai_client_from_config(self) -> Any:
        model = self._section("model")
        api_key = model.get("api_key")
        base_url = model.get("base_url")
        if not (api_key and base_url):
            raise RuntimeError("model.api_key / model.base_url are required for the OpenAI-compatible backend")
        import openai

        return openai.AsyncOpenAI(api_key=str(api_key), base_url=str(base_url).rstrip("/"))

    def _model_backend_from_config(self, client: Any) -> Any:
        model = self._section("model")
        model_name = model.get("model") or model.get("name")
        if not model_name:
            raise RuntimeError("model.model is required")
        defaults: dict[str, Any] = {}
        extra_body: dict[str, Any] = {}
        if model.get("thinking_enabled") is not None:
            extra_body["enable_thinking"] = bool(model.get("thinking_enabled"))
        if model.get("thinking_budget") is not None:
            extra_body["thinking_budget"] = int(model["thinking_budget"])
        if extra_body:
            defaults["extra_body"] = extra_body
        return OpenAIBackend(client=client, model=str(model_name), **defaults)

    def _fast_backend_from_config(self) -> Any | None:
        fast = self._section("fast_model")
        model_name = fast.get("model") or fast.get("name")
        if not model_name:
            return None
        model = self._section("model")
        api_key = fast.get("api_key") or model.get("api_key")
        base_url = fast.get("base_url") or model.get("base_url")
        if not (api_key and base_url):
            raise RuntimeError("fast_model.model requires fast_model.api_key/base_url or model fallback values")
        import openai

        client = openai.AsyncOpenAI(api_key=str(api_key), base_url=str(base_url).rstrip("/"))
        return OpenAIBackend(client=client, model=str(model_name), extra_body={"enable_thinking": False})

    def _progress_summarizer_from_config(self) -> Any | None:
        backend = self._fast_backend_from_config()
        if backend is None:
            return None
        fast = self._section("fast_model")
        return build_progress_summarizer(
            backend,
            timeout_seconds=float(fast.get("timeout_seconds") or 3.0),
            max_chars=int(fast.get("max_chars") or 60),
        )

    def _text_summarizer_from_config(self) -> Any | None:
        backend = self._fast_backend_from_config()
        if backend is None:
            return None
        fast = self._section("fast_model")
        return build_fast_text_summarizer(
            backend,
            timeout_seconds=float(fast.get("summary_timeout_seconds") or 12.0),
            default_max_chars=int(fast.get("summary_max_chars") or 2000),
        )

    def _timezone_resolver(self) -> Any:
        return build_timezone_resolver(
            str(self._get("timezone", "Asia/Shanghai") or "Asia/Shanghai"),
            user_tokens=self.provider,
            client=self.client,
        )

    def _system_prompt(self, timezone_resolver: Any) -> Any:
        system = self._get("system")
        if system is None:
            path = self._get("system_path")
            system = Path(path).read_text(encoding="utf-8") if path else None
        if system is None:
            return None
        if callable(system):
            return system
        if bool(self._get("time_aware_system", True)):
            return build_time_aware_system_prompt(str(system), timezone_resolver)
        return str(system)

    def _describe_analyzer(self) -> Any:
        model_name = self._section("model").get("model") or self._section("model").get("name")
        if not model_name:
            return None

        async def analyzer(data: bytes, *, media_type: str | None, name: str | None) -> str:
            analysis = await analyze_attachment(
                data,
                {"name": name, "mime_type": media_type},
                extractor=self.extractor,
                openai_client=self.model_client,
                model=str(model_name),
            )
            return json.dumps(analysis, ensure_ascii=False)

        return analyzer

    def _oauth_state_signer(self) -> OAuthStateSigner | None:
        secret = self._get("oauth.state_secret")
        if not secret:
            secret = self._get("feishu.app_secret")
            if secret:
                secret = hmac.new(str(secret).encode(), b"feishu-agent-oauth-state-v1", hashlib.sha256).hexdigest()
                logger.warning(
                    "oauth.state_secret is not set; deriving the OAuth state key from feishu.app_secret. "
                    "Set a dedicated oauth.state_secret in production."
                )
        if not secret:
            return None
        return OAuthStateSigner(str(secret), ttl_seconds=int(self._get("oauth.state_ttl_seconds", 3600) or 3600))

    def _authorize_url_builder(self) -> Any | None:
        if self.signer is not None and self.oauth_redirect_uri and self.provider is not None:
            return build_authorize_url_builder(self.provider, self.signer, self.oauth_redirect_uri)
        if not self.oauth_redirect_uri:
            logger.warning("oauth.public_url is not set; user-scoped tools cannot offer an authorize link")
        return None

    def _section(self, name: str) -> Mapping[str, Any]:
        value = self.config.get(name) if isinstance(self.config, Mapping) else None
        return value if isinstance(value, Mapping) else {}

    def _get(self, path: str, default: Any = None) -> Any:
        node: Any = self.config
        for part in path.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return default
            node = node[part]
        return node


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, Sequence):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value),)


__all__ = ["Agent"]
