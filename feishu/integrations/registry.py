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

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from starlette.routing import BaseRoute

from ..agent.bundles import BUNDLES

if TYPE_CHECKING:
    from ..gateway import GatewayContext


class GatewayIntegration(Protocol):
    r"""An external system that contributes routes to a Feishu gateway."""

    def routes(self, context: GatewayContext) -> Sequence[BaseRoute]: ...


GatewayIntegrationFactory = Callable[..., GatewayIntegration]


class IntegrationRegistry:
    r"""Registers bundled tool and gateway integrations explicitly."""

    def __init__(self) -> None:
        self._tool_bundles: dict[str, type[Any]] = {}
        self._gateway_factories: dict[str, GatewayIntegrationFactory] = {}

    @property
    def tool_bundle_names(self) -> tuple[str, ...]:
        return tuple(self._tool_bundles)

    @property
    def gateway_names(self) -> tuple[str, ...]:
        return tuple(self._gateway_factories)

    def register_tool_bundle(self, name: str, bundle: type[Any], *, override: bool = False) -> None:
        if name in self._tool_bundles and not override:
            raise ValueError(f"tool integration {name!r} is already registered")
        BUNDLES.register(bundle, name=name, override=override)
        self._tool_bundles[name] = bundle

    def register_gateway(self, name: str, factory: GatewayIntegrationFactory, *, override: bool = False) -> None:
        if name in self._gateway_factories and not override:
            raise ValueError(f"gateway integration {name!r} is already registered")
        self._gateway_factories[name] = factory

    def build_gateway(self, name: str, /, *args: Any, **kwargs: Any) -> GatewayIntegration:
        try:
            factory = self._gateway_factories[name]
        except KeyError as exc:
            available = ", ".join(self.gateway_names) or "<none>"
            raise ValueError(f"unknown gateway integration {name!r}; registered integrations: {available}") from exc
        return factory(*args, **kwargs)
