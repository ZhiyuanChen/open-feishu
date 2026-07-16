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

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette

from ..client import FeishuClient
from ..events import EventDispatcher, InMemorySeenStore, SeenStore, create_card_route, create_event_route
from .config import GatewayConfig
from .routes import create_gateway_routes

if TYPE_CHECKING:
    from ..integrations import GatewayIntegration


@dataclass(frozen=True)
class GatewayContext:
    r"""供部署方在网关上挂载事件处理器的配置上下文。"""

    config: GatewayConfig
    client: Any
    seen_store: SeenStore


GatewayConfigure = Callable[[GatewayContext], EventDispatcher | None]


def create_gateway(
    config: GatewayConfig,
    *,
    configure: GatewayConfigure | None = None,
    client: Any | None = None,
    integrations: Sequence[GatewayIntegration] = (),
) -> Starlette:
    r"""创建一个轻量的 Starlette 飞书网关应用。"""
    if not config.service_keys:
        raise ValueError("GatewayConfig.service_keys must contain at least one service API key")
    if configure is not None and config.encrypt_key is None:
        raise ValueError("GatewayConfig.encrypt_key is required when configure is provided")

    owns_client = client is None
    feishu_client = client or FeishuClient(
        config.app_id,
        config.app_secret,
        region=config.region,
        base_url=config.base_url,
        accounts_url=config.accounts_url,
    )
    seen_store = InMemorySeenStore()
    routes = create_gateway_routes(config, feishu_client)
    context = GatewayContext(config, feishu_client, seen_store)
    for integration in integrations:
        routes.extend(integration.routes(context))

    dispatcher: EventDispatcher | None = None
    if config.encrypt_key is not None:
        configured = configure(context) if configure is not None else None
        dispatcher = configured or EventDispatcher()
        routes.extend(
            [
                create_event_route(
                    dispatcher,
                    encrypt_key=config.encrypt_key,
                    verification_token=config.verification_token,
                    seen_store=seen_store,
                    max_age_seconds=config.event_max_age_seconds,
                ),
                create_card_route(
                    dispatcher,
                    encrypt_key=config.encrypt_key,
                    verification_token=config.verification_token,
                    seen_store=seen_store,
                    max_age_seconds=config.event_max_age_seconds,
                ),
            ]
        )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        app.state.feishu_client = feishu_client
        app.state.seen_store = seen_store
        app.state.event_dispatcher = dispatcher
        try:
            yield
        finally:
            if owns_client:
                close = getattr(feishu_client, "aclose", None)
                if close is not None:
                    await close()

    return Starlette(routes=routes, lifespan=lifespan)
