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

import asyncio
import html
import inspect
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal
from uuid import uuid4

from ..auth import OAuthStateSigner, UserTokenProvider, user_from_identity_keys, user_identity_keys
from ..events.envelope import Event
from ._callbacks import accepts_positional_arguments
from ._flow import (
    authorization_card_message_id,
    authorization_complete_card,
    suspension_progress_note,
)
from .context import current_tool_context, use_tool_context
from .llm import Message, ToolCall, ToolResultPart, parse_tool_arguments
from .progress import _message_id_from_response, _pending_progress_extra, _progress_message_id, _ProgressCard
from .result import ToolOutcome, ToolResult, coerce_tool_result
from .session import ClaimResult, PendingAuthorization
from .tools import Tool

logger = logging.getLogger("feishu")

AuthorizeUrlBuilder = Callable[[Mapping[str, Any], Sequence[str], Any | None], str | None]
AuthorizationResumeCallback = Callable[..., Any]
AWAITING_AUTHORIZATION_PROGRESS_TEXT = "等待用户授权。"
_RESUME_TASKS: set[asyncio.Task[Any]] = set()


def build_authorize_url_builder(
    provider: UserTokenProvider, signer: OAuthStateSigner, redirect_uri: str
) -> AuthorizeUrlBuilder:
    r"""构建注入到工具上下文里的本轮授权 URL 生成器。"""

    def builder(user: Mapping[str, Any], scopes: Sequence[str], authorization: Any | None = None) -> str | None:
        keys = user_identity_keys(user)
        if not keys:
            return None
        authorization_id = getattr(authorization, "authorization_id", None)
        extra = {"authorization_id": authorization_id} if authorization_id else None
        state = signer.issue(user_keys=keys, scopes=tuple(scopes), extra=extra)
        return provider.authorize_url(redirect_uri, scope=list(scopes) or None, state=state)

    return builder


async def preflight_authorization(
    agent: Any,
    event: Event,
    session_id: str,
    history: list[Message],
    call: ToolCall,
    tool: Tool,
    progress: _ProgressCard,
) -> Literal["suspended", "blocked"] | None:
    r"""工具执行 / 审批前检查用户授权；缺授权时先发授权卡片并挂起本轮。"""
    scopes = tuple(tool.auth_scopes)
    if not scopes or await current_tool_context().has_user_auth(scopes):
        return None
    missing_auth = ToolResult(
        ToolOutcome.NEEDS_USER_AUTH,
        content="user authorization required",
        auth_scopes=scopes,
        is_error=True,
    )
    if await request_authorization(agent, event, session_id, history, call, missing_auth, progress):
        return "suspended"
    await agent._record_tool_result_part(
        session_id,
        history,
        ToolResultPart(
            tool_call_id=call.id,
            content="user authorization required, but I could not send an authorization card",
            is_error=True,
        ),
    )
    return "blocked"


async def request_authorization(
    agent: Any,
    event: Event,
    session_id: str,
    history: list[Message],
    call: ToolCall,
    result: ToolResult,
    progress: _ProgressCard | None = None,
) -> bool:
    r"""
    为缺少用户授权的工具创建挂起授权并发送授权卡片；返回是否已挂起本轮。

    挂起记录先于卡片送达落库；OAuth callback 成功后调用 `resume_authorization` 恢复原工具调用。
    """
    initiator = current_tool_context().requesting_user()
    owner_user_keys = user_identity_keys(initiator)
    message = event.body.get("message") or {}
    chat_id = message.get("chat_id")
    if not owner_user_keys or agent.client is None or not chat_id or agent._auth_card_builder is None:
        return False
    authorization = PendingAuthorization(
        authorization_id=uuid4().hex,
        session_id=session_id,
        tool_call_id=call.id,
        tool_name=call.name,
        arguments=parse_tool_arguments(call.arguments),
        scopes=tuple(result.auth_scopes),
        owner_user_keys=owner_user_keys,
        tenant_key=getattr(event, "tenant_key", None),
        chat_id=chat_id,
        created_message_id=message.get("message_id"),
        created_event_id=getattr(event, "event_id", None) or None,
        created_at=int(time.time()),
        extra=_pending_progress_extra(progress),
    )
    authorize_url = build_authorize_url(agent, initiator, authorization.scopes, authorization)
    if not authorize_url:
        return False
    try:
        card = agent._auth_card_builder(authorize_url)
    except Exception:  # noqa: BLE001 - product card builder errors should fall back to the old auth path
        logger.warning("failed to build auth card", exc_info=True)
        return False
    try:
        await agent.authorizations.put(authorization)
    except Exception:  # noqa: BLE001 - no persisted pending means callback cannot resume safely
        logger.warning("failed to persist pending authorization", exc_info=True)
        return False
    try:
        response = await agent.client.im.send(chat_id, card, msg_type="interactive", receive_id_type="chat_id")
    except Exception:  # noqa: BLE001 - undeliverable card -> cancel the pending, then fall back
        logger.warning("failed to send auth card; cancelling pending %s", authorization.authorization_id, exc_info=True)
        try:
            await agent.authorizations.complete(authorization.authorization_id, outcome="cancelled")
        except Exception:  # noqa: BLE001 - best-effort cleanup; an uncancelled pending will TTL-expire
            logger.warning(
                "failed to cancel pending authorization %s after card send failure",
                authorization.authorization_id,
                exc_info=True,
            )
        return False
    auth_card_message_id = _message_id_from_response(response)
    if auth_card_message_id:
        # Do not put the whole authorization again: in multi-worker deployments, the callback can claim this
        # row right after the card is sent, and writing the stale object back would resurrect executing as
        # awaiting. The in-memory store keeps this field by reference; durable stores can still resume safely,
        # with only auth-card cleanup degraded.
        authorization.extra = {**authorization.extra, "auth_card_message_id": auth_card_message_id}
    return True


def build_authorize_url(
    agent: Any, user: Mapping[str, Any], scopes: tuple[str, ...], authorization: PendingAuthorization
) -> str | None:
    r"""调用产品注入的授权 URL 构造器，兼容二参旧签名与三参可恢复授权签名。"""
    builder = agent.authorize_url_builder
    if builder is None:
        return None
    if accepts_positional_arguments(builder, 3):
        return builder(user, scopes, authorization)
    return builder(user, scopes)


async def send_auth_card(agent: Any, event: Event, authorize_url: str) -> bool:
    r"""向当前会话发送一张授权卡片；缺少构造器、客户端或 chat 时返回 `False`。"""
    if agent._auth_card_builder is None or agent.client is None or not authorize_url:
        return False
    message = event.body.get("message") or {}
    chat_id = message.get("chat_id")
    if not chat_id:
        return False
    card = agent._auth_card_builder(authorize_url)
    await agent.client.im.send(chat_id, card, msg_type="interactive", receive_id_type="chat_id")
    return True


async def try_send_auth_card(agent: Any, event: Event, authorize_url: str) -> bool:
    r"""发送授权卡片的不抛错封装：发送失败只记日志并返回 `False`。"""
    try:
        return await send_auth_card(agent, event, authorize_url)
    except Exception:  # noqa: BLE001 — auth-card delivery must never crash the turn or drop the tool result
        logger.warning("failed to send auth card; returning the URL inline", exc_info=True)
        return False


async def resume_authorization(agent: Any, authorization_id: str, *, user: Mapping[str, Any] | None = None) -> str:
    r"""
    OAuth callback 成功保存用户 token 后，恢复一次挂起授权对应的原工具调用。

    返回机器可读状态；面向用户的最终结果发回原飞书会话。
    """
    authorization = await agent.authorizations.get(authorization_id)
    if authorization is None:
        return "missing"
    if user is None:
        await notify_authorization_resume_problem(
            agent,
            authorization,
            "授权已完成，但无法确认完成授权的用户身份。请重新发起请求。",
        )
        return "forbidden"
    callback_keys = set(user_identity_keys(user))
    if not authorization.owner_user_keys or not (callback_keys & set(authorization.owner_user_keys)):
        return "forbidden"
    claim = await agent.authorizations.claim(authorization_id)
    if claim is not ClaimResult.CLAIMED:
        if claim in (ClaimResult.EXPIRED, ClaimResult.MISSING):
            await remove_authorization_card(agent, authorization)
            await notify_authorization_resume_problem(
                agent,
                authorization,
                "授权已完成，但原请求已过期。请再告诉我一次。",
            )
        return claim.value

    await remove_authorization_card(agent, authorization)

    resume_event = event_from_pending_authorization(authorization)
    context = agent._tool_context(resume_event)
    if authorization.owner_user_keys:
        context.user = user_from_identity_keys(authorization.owner_user_keys)
    with use_tool_context(context):
        try:
            progress = _ProgressCard(agent, resume_event)
            progress.reuse(_progress_message_id(authorization.extra))
            await progress.step(
                authorization.tool_name,
                description=agent._tool_description(authorization.tool_name),
            )
            try:
                result = await agent.registry.dispatch(authorization.tool_name, authorization.arguments)
            except Exception as exc:  # noqa: BLE001 - report tool failure to the model, not the browser
                logger.warning(
                    "authorization %s: tool %s raised %s during resume",
                    authorization.authorization_id,
                    authorization.tool_name,
                    type(exc).__name__,
                )
                result = ToolResult(
                    ToolOutcome.FAILED,
                    content=(
                        f"tool {authorization.tool_name} failed with {type(exc).__name__}; "
                        "see server logs for details"
                    ),
                    is_error=True,
                )
            content, is_error, _ = coerce_tool_result(result)
            result_part = ToolResultPart(
                tool_call_id=authorization.tool_call_id,
                content=content,
                is_error=is_error,
            )
            async with agent._session_lock(authorization.session_id):
                history = await agent.store.get(authorization.session_id)
                await agent._record_tool_result_part(authorization.session_id, history, result_part)
                suspension = await agent._continue_tool_calls_after(
                    resume_event,
                    authorization.session_id,
                    history,
                    authorization.tool_call_id,
                    progress,
                )
                if suspension:
                    await progress.finalize(suspension_progress_note(suspension))
                else:
                    await agent._loop(resume_event, authorization.session_id, history, progress=progress)
            await agent.authorizations.complete(authorization_id, outcome="failed" if is_error else "executed")
            return "resumed"
        except Exception:  # noqa: BLE001 - callback background failures should be reported in chat and logged
            await agent.authorizations.complete(authorization_id, outcome="frozen")
            logger.exception(
                "authorization %s: error resuming tool %s",
                authorization.authorization_id,
                authorization.tool_name,
            )
            try:
                await agent._finalize(
                    resume_event,
                    "授权已完成，但我无法继续原请求。请重新发起请求。",
                )
            except Exception:  # noqa: BLE001 - nothing more to do; logs already have the underlying failure
                logger.debug("could not send authorization resume failure", exc_info=True)
            return "failed"


async def notify_authorization_resume_problem(agent: Any, authorization: PendingAuthorization, text: str) -> None:
    r"""在 OAuth 回调无法恢复已知 pending authorization 时，尽力向聊天里反馈状态。"""
    try:
        await agent._finalize(event_from_pending_authorization(authorization), text)
    except Exception:  # noqa: BLE001 - callback path should not fail because chat feedback failed
        logger.debug(
            "could not send authorization resume status for %s",
            authorization.authorization_id,
            exc_info=True,
        )


async def remove_authorization_card(agent: Any, authorization: PendingAuthorization) -> None:
    r"""授权完成后尽力清理独立 OAuth 授权卡片。"""
    message_id = authorization_card_message_id(authorization.extra)
    if agent.client is None or not message_id:
        return
    try:
        await agent.client.im.recall(message_id)
        return
    except Exception:  # noqa: BLE001 - deletion is UI cleanup; fall back to making the card inert
        logger.debug("could not recall authorization card %s", message_id, exc_info=True)
    try:
        await agent.client.im.patch(message_id, authorization_complete_card())
    except Exception:  # noqa: BLE001 - never let cleanup affect the resumed tool
        logger.debug("could not patch authorization card %s", message_id, exc_info=True)


def event_from_pending_authorization(authorization: PendingAuthorization) -> Event:
    r"""从挂起授权记录重建原会话事件，供工具恢复与最终回复定位。"""
    return Event.from_payload(
        {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "message_id": authorization.created_message_id,
                    "chat_id": authorization.chat_id,
                }
            },
        }
    )


def oauth_callback_handler(
    provider: UserTokenProvider,
    signer: OAuthStateSigner,
    redirect_uri: str,
    *,
    on_authorized: AuthorizationResumeCallback | None = None,
    success_message: str = "授权成功，正在回到飞书继续处理。",
    success_title: str = "授权完成",
    invalid_state_message: str = "授权校验失败：state 无效或已过期，请重新发起授权。",
    missing_code_message: str = "授权失败：回调缺少 code。",
    exchange_error_message: str = "授权失败：换取用户凭证时出错，请稍后再试。",
    user_mismatch_message: str = "授权失败：完成授权的用户与发起授权的用户不一致。",
) -> Callable[[Any], Any]:
    r"""创建用于用户态工具授权回调的 Starlette 兼容处理函数。"""

    async def handle(request: Any) -> Any:
        from starlette.responses import PlainTextResponse

        code = request.query_params.get("code")
        state = signer.consume(request.query_params.get("state"))
        if state is None:
            return PlainTextResponse(invalid_state_message, status_code=400)
        if not code:
            return PlainTextResponse(missing_code_message, status_code=400)
        try:
            token_data = await provider.client.oauth.exchange_code(code, redirect_uri=redirect_uri)
            info = await provider.client.oauth.user_info(token_data.get("access_token"))
        except Exception:
            logger.exception("oauth callback: failed to exchange code")
            return PlainTextResponse(exchange_error_message, status_code=400)
        if not signer.user_matches(state, dict(info)):
            return PlainTextResponse(user_mismatch_message, status_code=403)
        await provider.store.save(token_data, user_info=dict(info))
        authorization_id = str(state.extra.get("authorization_id") or "")
        if on_authorized is not None and authorization_id:
            _spawn_authorization_resume(on_authorized, authorization_id, dict(info))
        return _auto_close_response(success_message, title=success_title)

    return handle


def _spawn_authorization_resume(
    callback: AuthorizationResumeCallback, authorization_id: str, user: Mapping[str, Any]
) -> None:
    async def run() -> None:
        try:
            result = callback(authorization_id, user=user)
            if inspect.isawaitable(result):
                result = await result
            logger.info("oauth callback: authorization resume %s -> %s", authorization_id, result)
        except Exception:  # noqa: BLE001 - callback request already completed; log background failures
            logger.exception("oauth callback: failed to resume authorization %s", authorization_id)

    task = asyncio.create_task(run())
    _RESUME_TASKS.add(task)
    task.add_done_callback(_RESUME_TASKS.discard)


def _auto_close_response(message: str, *, title: str = "授权完成") -> Any:
    from starlette.responses import HTMLResponse

    safe_title = html.escape(title)
    safe_message = html.escape(message)
    html_body = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
</head>
<body>
  <p>{safe_message}</p>
  <p>如果这个页面没有自动关闭，可以直接关掉它。</p>
  <script>
    window.close();
  </script>
</body>
</html>"""
    return HTMLResponse(html_body)


__all__ = [
    "AWAITING_AUTHORIZATION_PROGRESS_TEXT",
    "AuthorizationResumeCallback",
    "AuthorizeUrlBuilder",
    "build_authorize_url_builder",
    "oauth_callback_handler",
]
