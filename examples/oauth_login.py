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

"""最小飞书用户 OAuth 登录桥接示例（Starlette）。

本示例用一个只有两个路由的小型 Web 应用演示真实的 ``open-feishu`` 用户 OAuth 流程：

  GET /login    -> 用 [feishu.auth.oauth.OAuthNamespace.authorize_url][] 构造授权页 URL，
                   并把随机 CSRF ``state`` 写入 cookie，供回调校验。
  GET /callback -> 校验 ``state``，用 [feishu.auth.oauth.OAuthNamespace.exchange_code][]
                   把返回的 ``code`` 换成用户 token，再通过
                   [feishu.auth.oauth.OAuthNamespace.user_info][] 读取登录用户资料并渲染
                   name + open_id。

SDK 的 OAuthNamespace 是无状态的：真实应用维护 session / 用户级 token 存储；本示例在读取
profile 后丢弃 token，页面渲染 name + open_id 等非密字段。access_token / refresh_token /
app secret 保持在服务端流程内。

必需飞书应用权限（在应用后台申请）
----------------------------------
  * contact:user.base:readonly   -> name / avatar（基础展示字段）
  * offline_access               -> 签发 refresh_token，后续可通过 client.oauth.refresh 刷新用户 token
可选权限（申请后 user_info 返回对应字段）：
  * contact:user.email:readonly  -> email
  * contact:user.phone:readonly  -> mobile
email / enterprise_email / mobile / user_id 等敏感字段只会在授予对应权限后出现在 user_info。

运行方式
--------
  pip install open-feishu uvicorn          # starlette 随 open-feishu 安装；uvicorn 需单独安装
  export FEISHU_APP_ID=cli_xxx
  export FEISHU_APP_SECRET=xxxx
  # 可选：覆盖 redirect URI（必须与控制台登记值完全一致）
  export FEISHU_REDIRECT_URI=http://localhost:8000/callback
  uvicorn examples.oauth_login:app --port 8000
  # 然后在浏览器打开 http://localhost:8000/login
"""

from __future__ import annotations

import html
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from feishu import FeishuClient

# This redirect_uri MUST be pre-registered in the Feishu app console and match
# byte-for-byte (scheme, host, port, path) or Feishu rejects the callback.
REDIRECT_URI = os.getenv("FEISHU_REDIRECT_URI", "http://localhost:8000/callback")

# Scopes requested at consent time. offline_access is what makes Feishu mint a
# refresh_token; contact:user.base:readonly is what lets user_info return a name.
SCOPES = ["contact:user.base:readonly", "offline_access"]

# Name of the cookie used to carry the CSRF state from /login to /callback.
STATE_COOKIE = "feishu_oauth_state"

_client: FeishuClient | None = None


def get_client() -> FeishuClient:
    """返回懒加载的 FeishuClient，避免模块导入阶段读取凭证。"""
    global _client
    if _client is None:
        # Credentials are read from FEISHU_APP_ID / FEISHU_APP_SECRET by the client itself. OAuth needs an
        # InternalCredential (app_id/app_secret); the client builds one from the environment.
        _client = FeishuClient()
    return _client


async def login(request: Request) -> RedirectResponse:
    """启动登录流程：生成新的 CSRF state，并重定向到飞书授权页。"""
    state = secrets.token_urlsafe(16)
    # authorize_url is a pure string builder -- no network, not a coroutine.
    url = get_client().oauth.authorize_url(REDIRECT_URI, scope=SCOPES, state=state)
    response = RedirectResponse(url)
    response.set_cookie(STATE_COOKIE, state, httponly=True, samesite="lax", max_age=600)
    return response


async def callback(request: Request) -> HTMLResponse:
    """处理飞书回调：校验 state、交换 code，并渲染用户资料。"""
    error = request.query_params.get("error")
    if error:
        # Escape: `error` comes straight from the redirect query string (attacker-controlled).
        return HTMLResponse(f"<h1>Authorization failed</h1><p>{html.escape(error)}</p>", status_code=400)

    # CSRF check: the state Feishu echoes back must match the one we set.
    expected = request.cookies.get(STATE_COOKIE)
    returned = request.query_params.get("state")
    if not expected or returned != expected:
        return HTMLResponse("<h1>Invalid state</h1>", status_code=400)

    code = request.query_params.get("code")
    if not code:
        return HTMLResponse("<h1>Missing authorization code</h1>", status_code=400)

    # Swap code -> user token envelope. redirect_uri must match the one used at /login.
    client = get_client()
    tokens = await client.oauth.exchange_code(code, redirect_uri=REDIRECT_URI)
    access_token = tokens["access_token"]
    # A refresh_token is present only if offline_access was granted. It rotates and
    # is single-use: a real app would persist it now (we intentionally do not).
    # refresh_token = tokens.get("refresh_token")  # persist this per-session

    # Read the signed-in user's profile with their user_access_token as Bearer.
    profile = await client.oauth.user_info(access_token)
    # Escape every provider-supplied field before HTML interpolation: a user's
    # Feishu display name is user-controlled, so rendering it raw would be XSS.
    name = html.escape(profile.get("name") or "(name scope not granted)")
    open_id = html.escape(profile.get("open_id") or "(unavailable)")

    # Render ONLY non-secret identity fields. Never echo tokens or the app secret.
    return HTMLResponse(f"<h1>Logged in</h1><p>Name: {name}</p><p>open_id: {open_id}</p>")


# When the process exits, close the client's underlying HTTP transport.
async def _shutdown() -> None:
    if _client is not None:
        await _client.aclose()


@asynccontextmanager
async def _lifespan(_app: Starlette) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await _shutdown()


app = Starlette(
    routes=[
        Route("/login", login),
        Route("/callback", callback),
    ],
    lifespan=_lifespan,
)


if __name__ == "__main__":
    # Convenience launcher; equivalent to: uvicorn examples.oauth_login:app
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
