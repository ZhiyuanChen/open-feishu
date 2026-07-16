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

from collections.abc import Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from ..errors import FeishuError
from .auth import ServiceAuthError, ServiceCapabilityError, require_service_capability
from .config import GatewayConfig
from .errors import GatewayRequestError, error_response, feishu_error_response, read_json_object

_MISSING: Any = object()
GatewayEndpoint = Callable[[Request], Any]


def create_gateway_routes(config: GatewayConfig, client: Any) -> list[BaseRoute]:
    r"""创建网关内部的健康检查、消息发送与组织信息路由。"""

    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def ready(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    return [
        Route("/healthz", health, methods=["GET"]),
        Route("/readyz", ready, methods=["GET"]),
        Route("/messages/send", _internal(config, _send_message(client)), methods=["POST"]),
        Route("/messages/card", _internal(config, _send_card(client)), methods=["POST"]),
        Route("/org/users", _internal(config, _list_users(client)), methods=["GET"]),
        Route("/org/users/{user_id}", _internal(config, _get_user(client)), methods=["GET"]),
        Route("/org/departments", _internal(config, _list_departments(client)), methods=["GET"]),
        Route(
            "/org/departments/{department_id}",
            _internal(config, _get_department(client)),
            methods=["GET"],
        ),
        Route("/org/resolve", _internal(config, _resolve_users(client)), methods=["POST"]),
    ]


def _internal(config: GatewayConfig, endpoint: GatewayEndpoint) -> GatewayEndpoint:
    async def wrapped(request: Request) -> Response:
        try:
            require_service_capability(
                request,
                config.service_keys,
                config.service_capabilities,
            )
            return await endpoint(request)
        except ServiceAuthError:
            return error_response("unauthorized", status_code=401)
        except ServiceCapabilityError:
            return error_response("forbidden", status_code=403)
        except GatewayRequestError as exc:
            return error_response(exc.message, status_code=exc.status_code)
        except FeishuError as exc:
            return feishu_error_response(exc)

    return wrapped


def _send_message(client: Any) -> GatewayEndpoint:
    async def endpoint(request: Request) -> Response:
        payload = await read_json_object(request)
        receive_id = _required_str(payload, "receive_id")
        content = _message_content(payload, "content")
        kwargs = _message_kwargs(payload)
        data = await client.im.send(receive_id, content, **kwargs)
        return JSONResponse(data)

    return endpoint


def _send_card(client: Any) -> GatewayEndpoint:
    async def endpoint(request: Request) -> Response:
        payload = await read_json_object(request)
        receive_id = _required_str(payload, "receive_id")
        content = _message_content(payload, "card", fallback_key="content")
        kwargs = _message_kwargs(payload)
        kwargs["msg_type"] = "interactive"
        data = await client.im.send(receive_id, content, **kwargs)
        return JSONResponse(data)

    return endpoint


def _list_users(client: Any) -> GatewayEndpoint:
    async def endpoint(request: Request) -> Response:
        params = request.query_params
        kwargs: dict[str, Any] = {
            "user_id_type": params.get("user_id_type", "open_id"),
            "department_id_type": params.get("department_id_type", "open_department_id"),
        }
        page_size = _query_int(request, "page_size")
        max_items = _query_int(request, "max_items")
        if page_size is not None:
            kwargs["page_size"] = page_size
        if max_items is not None:
            kwargs["max_items"] = max_items
        users = await client.contact.users.list(params.get("department_id", "0"), **kwargs)
        return JSONResponse(users)

    return endpoint


def _get_user(client: Any) -> GatewayEndpoint:
    async def endpoint(request: Request) -> Response:
        params = request.query_params
        user = await client.contact.users.get(
            request.path_params["user_id"],
            user_id_type=params.get("user_id_type", "open_id"),
            department_id_type=params.get("department_id_type", "open_department_id"),
        )
        return JSONResponse(user)

    return endpoint


def _list_departments(client: Any) -> GatewayEndpoint:
    async def endpoint(request: Request) -> Response:
        params = request.query_params
        kwargs: dict[str, Any] = {
            "department_id_type": params.get("department_id_type", "open_department_id"),
        }
        fetch_child = _query_bool(request, "fetch_child")
        page_size = _query_int(request, "page_size")
        max_items = _query_int(request, "max_items")
        if fetch_child is not None:
            kwargs["fetch_child"] = fetch_child
        if page_size is not None:
            kwargs["page_size"] = page_size
        if max_items is not None:
            kwargs["max_items"] = max_items
        departments = await client.contact.departments.list(params.get("department_id", "0"), **kwargs)
        return JSONResponse(departments)

    return endpoint


def _get_department(client: Any) -> GatewayEndpoint:
    async def endpoint(request: Request) -> Response:
        department = await client.contact.departments.get(
            request.path_params["department_id"],
            department_id_type=request.query_params.get("department_id_type", "open_department_id"),
        )
        return JSONResponse(department)

    return endpoint


def _resolve_users(client: Any) -> GatewayEndpoint:
    async def endpoint(request: Request) -> Response:
        payload = await read_json_object(request)
        emails = _optional_str_list(payload, "emails")
        mobiles = _optional_str_list(payload, "mobiles")
        include_resigned = payload.get("include_resigned", False)
        if not isinstance(include_resigned, bool):
            raise GatewayRequestError("include_resigned must be a boolean")
        data = await client.contact.users.batch_get_id(
            emails=emails,
            mobiles=mobiles,
            include_resigned=include_resigned,
        )
        return JSONResponse(data)

    return endpoint


def _message_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for key in ("receive_id_type", "msg_type", "uuid"):
        value = payload.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise GatewayRequestError(f"{key} must be a string")
            kwargs[key] = value
    return kwargs


def _message_content(payload: dict[str, Any], key: str, *, fallback_key: str | None = None) -> dict[str, Any] | str:
    value = payload.get(key, _MISSING)
    if value is _MISSING and fallback_key is not None:
        value = payload.get(fallback_key, _MISSING)
    if value is _MISSING:
        raise GatewayRequestError(f"{key} is required")
    if not isinstance(value, dict | str):
        raise GatewayRequestError(f"{key} must be a JSON object or string")
    return value


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise GatewayRequestError(f"{key} must be a non-empty string")
    return value


def _optional_str_list(payload: dict[str, Any], key: str) -> list[str] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise GatewayRequestError(f"{key} must be a list of strings")
    return value


def _query_int(request: Request, key: str) -> int | None:
    value = request.query_params.get(key)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise GatewayRequestError(f"{key} must be an integer") from exc


def _query_bool(request: Request, key: str) -> bool | None:
    value = request.query_params.get(key)
    if value is None or value == "":
        return None
    normalized = value.lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise GatewayRequestError(f"{key} must be a boolean")
