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

import os
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GatewayConfig:
    r"""可部署飞书网关的配置对象。"""

    app_id: str
    app_secret: str
    service_keys: dict[str, str] = field(default_factory=dict)
    service_capabilities: dict[str, frozenset[str]] = field(default_factory=dict)
    region: str = "feishu"
    base_url: str | None = None
    accounts_url: str | None = None
    encrypt_key: str | None = None
    verification_token: str | None = None
    event_max_age_seconds: float | None = 300.0
    host: str = "0.0.0.0"
    port: int = 8000

    def __post_init__(self) -> None:
        _validate_service_capabilities(self.service_keys, self.service_capabilities)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> GatewayConfig:
        r"""从环境变量构建网关配置。"""
        env = environ or os.environ
        app_id = _required(env, "FEISHU_APP_ID")
        app_secret = _required(env, "FEISHU_APP_SECRET")
        return cls(
            app_id=app_id,
            app_secret=app_secret,
            service_keys=parse_service_keys(env.get("FEISHU_GATEWAY_SERVICE_KEYS", "")),
            service_capabilities=parse_service_capabilities(env.get("FEISHU_GATEWAY_SERVICE_CAPABILITIES", "")),
            region=env.get("FEISHU_REGION", "feishu") or "feishu",
            base_url=env.get("FEISHU_BASE_URL") or None,
            accounts_url=env.get("FEISHU_ACCOUNTS_URL") or None,
            encrypt_key=env.get("FEISHU_ENCRYPT_KEY") or None,
            verification_token=env.get("FEISHU_VERIFICATION_TOKEN") or None,
            event_max_age_seconds=_optional_float(env.get("FEISHU_EVENT_MAX_AGE_SECONDS", "300")),
            host=env.get("FEISHU_GATEWAY_HOST", "0.0.0.0") or "0.0.0.0",
            port=_optional_int(env.get("FEISHU_GATEWAY_PORT"), default=8000),
        )


def parse_service_keys(raw: str) -> dict[str, str]:
    r"""把 ``service:key`` 列表解析为网关使用的 ``{key: service}`` 映射。"""
    service_keys: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        service_name, separator, api_key = item.partition(":")
        if not separator or not service_name or not api_key:
            raise ValueError("FEISHU_GATEWAY_SERVICE_KEYS entries must be service:key pairs")
        service_keys[api_key] = service_name
    return service_keys


def parse_service_capabilities(raw: str) -> dict[str, frozenset[str]]:
    r"""Parse ``service:capability|capability`` entries separated by semicolons."""
    service_capabilities: dict[str, frozenset[str]] = {}
    for item in raw.split(";"):
        item = item.strip()
        if not item:
            continue
        service_name, separator, raw_capabilities = item.partition(":")
        service_name = service_name.strip()
        if not separator or not service_name or not raw_capabilities:
            raise ValueError(
                "FEISHU_GATEWAY_SERVICE_CAPABILITIES entries must be " "service:capability|capability pairs"
            )
        if service_name in service_capabilities:
            raise ValueError("FEISHU_GATEWAY_SERVICE_CAPABILITIES cannot repeat a service")

        capabilities = frozenset(capability.strip() for capability in raw_capabilities.split("|") if capability.strip())
        if not capabilities:
            raise ValueError("FEISHU_GATEWAY_SERVICE_CAPABILITIES entries require a capability")
        for capability in capabilities:
            _validate_capability(capability)
        service_capabilities[service_name] = capabilities
    return service_capabilities


def _validate_service_capabilities(
    service_keys: Mapping[str, str], service_capabilities: Mapping[str, frozenset[str]]
) -> None:
    if not service_capabilities:
        return

    configured_services = set(service_keys.values())
    capability_services = set(service_capabilities)
    unknown = capability_services - configured_services
    if unknown:
        raise ValueError("FEISHU_GATEWAY_SERVICE_CAPABILITIES contains unknown services: " + ", ".join(sorted(unknown)))
    missing = configured_services - capability_services
    if missing:
        raise ValueError(
            "FEISHU_GATEWAY_SERVICE_CAPABILITIES missing capability entries for services: " + ", ".join(sorted(missing))
        )

    for capabilities in service_capabilities.values():
        if not capabilities:
            raise ValueError("FEISHU_GATEWAY_SERVICE_CAPABILITIES entries require a capability")
        for capability in capabilities:
            _validate_capability(capability)


def _validate_capability(capability: str) -> None:
    if not capability.startswith("/") or "?" in capability or "#" in capability:
        raise ValueError("FEISHU_GATEWAY_SERVICE_CAPABILITIES capabilities must be route paths")
    if "*" in capability and (capability.count("*") != 1 or not capability.endswith("/*")):
        raise ValueError("FEISHU_GATEWAY_SERVICE_CAPABILITIES wildcards must be terminal /* route prefixes")


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _optional_float(value: str | None) -> float | None:
    if value is None or value in ("", "0"):
        return None
    return float(value)


def _optional_int(value: str | None, *, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)
