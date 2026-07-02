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

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..errors import (
    FeishuApiError,
    FeishuAuthError,
    FeishuError,
    FeishuPermissionError,
    FeishuRateLimitError,
    FeishuServerError,
)


class GatewayRequestError(Exception):
    r"""可映射为 HTTP 响应的网关请求校验错误。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message, status_code)
        self.message = message
        self.status_code = status_code


def error_response(message: str, *, status_code: int) -> JSONResponse:
    r"""返回紧凑的 JSON 错误响应。"""
    return JSONResponse({"msg": message}, status_code=status_code)


async def read_json_object(request: Request) -> dict[str, Any]:
    r"""读取并校验请求体必须是 JSON 对象。"""
    try:
        payload = await request.json()
    except ValueError as exc:
        raise GatewayRequestError("invalid json") from exc
    if not isinstance(payload, dict):
        raise GatewayRequestError("request body must be a JSON object")
    return payload


def feishu_error_response(error: FeishuError) -> JSONResponse:
    r"""把 SDK 错误映射为网关 HTTP 响应，同时避免泄露凭据。"""
    status_code = 502
    if isinstance(error, FeishuPermissionError):
        status_code = 403
    elif isinstance(error, FeishuAuthError):
        status_code = 502
    elif isinstance(error, FeishuRateLimitError):
        status_code = 429
    elif isinstance(error, FeishuServerError):
        status_code = 502
    elif isinstance(error, FeishuApiError):
        status_code = 400
    payload: dict[str, Any] = {"msg": error.message, "code": error.code}
    if error.log_id:
        payload["log_id"] = error.log_id
    return JSONResponse(payload, status_code=status_code)
