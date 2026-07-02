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

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from chanfig import Registry

from ..tools import ToolRegistry


@dataclass(frozen=True)
class BundleContext:
    r"""构建工具 bundle 时可用的运行时服务与产品默认值。"""

    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"
    describe_analyzer: Any | None = None
    text_summarizer: Any | None = None
    mail_summary_max_messages: int = 10
    mail_summary_max_body_chars: int = 4000
    mail_summary_max_chars: int = 2000
    extra: dict[str, Any] = field(default_factory=dict)


class Bundle(Protocol):
    r"""可把一组工具注册进 [feishu.agent.tools.ToolRegistry][] 的命名 bundle。"""

    def register(self, registry: ToolRegistry, context: BundleContext) -> None: ...


BUNDLES = Registry()


def _available_bundle_names() -> tuple[str, ...]:
    names: list[str] = []

    def collect(prefix: str, registry: Registry) -> None:
        for key in registry.keys():
            value = registry.get(key)
            name = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, Registry):
                collect(name, value)
            else:
                names.append(name)

    collect("", BUNDLES)
    return tuple(sorted(names))


def build_tool_registry(
    bundles: Sequence[str],
    context: BundleContext | None = None,
    *,
    registry: ToolRegistry | None = None,
) -> ToolRegistry:
    r"""按已注册 bundle 名称构建运行时 [feishu.agent.tools.ToolRegistry][]。"""
    target = registry or ToolRegistry()
    bundle_context = context or BundleContext()
    for name in bundles:
        try:
            bundle = BUNDLES.build(name)
        except ValueError as exc:
            available = ", ".join(_available_bundle_names()) or "<none>"
            raise ValueError(f"unknown bundle {name!r}; registered bundles: {available}") from exc
        register = getattr(bundle, "register", None)
        if register is None:
            raise TypeError(f"bundle {name!r} does not define register(registry, context)")
        register(target, bundle_context)
    return target


__all__ = [
    "BUNDLES",
    "Bundle",
    "BundleContext",
    "build_tool_registry",
]
