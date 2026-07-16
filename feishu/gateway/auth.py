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

import hmac
from collections.abc import Collection, Mapping

from starlette.requests import Request


class ServiceAuthError(Exception):
    r"""内部网关请求缺少有效服务密钥时抛出。"""


class ServiceCapabilityError(Exception):
    r"""已认证服务没有调用当前路由的能力时抛出。"""


def require_service(request: Request, service_keys: Mapping[str, str]) -> str:
    r"""校验 ``Authorization: Bearer <key>`` 并返回对应的服务名。"""
    header = request.headers.get("authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        raise ServiceAuthError("missing bearer token")
    token = header[len(prefix) :].strip()
    if not token:
        raise ServiceAuthError("missing bearer token")

    matched_service: str | None = None
    for expected_key, service_name in service_keys.items():
        if hmac.compare_digest(token, expected_key):
            matched_service = service_name

    if matched_service is None:
        raise ServiceAuthError("invalid bearer token")
    request.state.service = matched_service
    return matched_service


def require_service_capability(
    request: Request,
    service_keys: Mapping[str, str],
    service_capabilities: Mapping[str, Collection[str]],
) -> str:
    r"""Validate a bearer key and, when configured, its route capability.

    Empty capability configuration preserves the legacy authenticated gateway
    behavior. Once an ACL is configured, every service must have an explicit
    route capability and unmatched requests are denied.
    """
    service = require_service(request, service_keys)
    if not service_capabilities:
        return service

    capabilities = service_capabilities.get(service)
    if capabilities is None or not any(_matches_capability(request.url.path, item) for item in capabilities):
        raise ServiceCapabilityError("service lacks required route capability")
    return service


def _matches_capability(route_path: str, capability: str) -> bool:
    if capability.endswith("/*"):
        return route_path.startswith(capability[:-1])
    return hmac.compare_digest(route_path, capability)
