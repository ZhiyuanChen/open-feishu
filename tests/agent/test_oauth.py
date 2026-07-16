from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import parse_qs, quote, urlsplit

from feishu.agent.oauth import (
    _RESUME_TASKS,
    build_authorize_url_builder,
    oauth_callback_handler,
    remove_authorization_card,
)
from feishu.agent.session import PendingAuthorization
from feishu.auth import OAuthStateSigner, UserTokenProvider


class _Provider:
    def __init__(self) -> None:
        self.client = SimpleNamespace(oauth=_OAuth())
        self.store = _Store()

    def authorize_url(
        self, redirect_uri: str, *, scope: str | list[str] | tuple[str, ...] | None = None, state: str | None = None
    ) -> str:
        scopes = ",".join(scope or [])
        return f"https://auth.example/authorize?redirect_uri={quote(redirect_uri)}&scope={quote(scopes)}&state={state}"


class _OAuth:
    async def exchange_code(self, code: str, *, redirect_uri: str | None = None) -> dict[str, str]:
        return {"access_token": "u_token"}

    async def user_info(self, access_token: str) -> dict[str, str]:
        return {"open_id": "ou_1"}


class _Store:
    def __init__(self) -> None:
        self.saved: list[tuple[dict[str, Any], dict[str, Any] | None, tuple[str, ...]]] = []

    async def save(
        self,
        token_data: dict[str, Any],
        *,
        user_info: dict[str, Any] | None = None,
        user_keys: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        self.saved.append((token_data, user_info, user_keys))
        return ("open_id:ou_1",)


def test_authorize_url_state_carries_pending_authorization_id() -> None:
    provider = _Provider()
    signer = OAuthStateSigner("secret")
    builder = build_authorize_url_builder(
        cast(UserTokenProvider, provider), signer, "https://agent.example/oauth/callback"
    )

    url = builder(
        {"open_id": "ou_1"},
        ("calendar:calendar",),
        SimpleNamespace(authorization_id="az_1"),
    )
    assert url is not None

    state = signer.consume(parse_qs(urlsplit(url).query)["state"][0])
    assert state is not None
    assert state.user_keys == ("open_id:ou_1",)
    assert state.scopes == ("calendar:calendar",)
    assert state.extra == {"authorization_id": "az_1"}


def test_oauth_callback_auto_closes_and_resumes_pending_authorization() -> None:
    async def run() -> None:
        _RESUME_TASKS.clear()
        provider = _Provider()
        signer = OAuthStateSigner("secret")
        state = signer.issue(user_keys=("open_id:ou_1",), extra={"authorization_id": "az_1"})
        request = SimpleNamespace(query_params={"code": "code_1", "state": state})
        resumed = []
        release = asyncio.Event()

        async def on_authorized(authorization_id: str, *, user: dict[str, Any]) -> str:
            resumed.append((authorization_id, user))
            await release.wait()
            return "resumed"

        handler = oauth_callback_handler(
            cast(UserTokenProvider, provider),
            signer,
            "https://agent.example/oauth/callback",
            on_authorized=on_authorized,
            success_message="授权成功，正在回到飞书继续处理。",
            success_title="授权完成",
        )

        response = await handler(request)
        await asyncio.sleep(0)

        assert response.status_code == 200
        assert b"window.close()" in response.body
        assert provider.store.saved
        assert len(_RESUME_TASKS) == 1
        assert resumed == [("az_1", {"open_id": "ou_1"})]
        tasks = list(_RESUME_TASKS)
        release.set()
        await asyncio.gather(*tasks)
        assert not _RESUME_TASKS

    asyncio.run(run())


def test_remove_authorization_card_patches_completion_before_recall() -> None:
    async def run() -> None:
        client = _CleanupClient()
        authorization = PendingAuthorization(
            authorization_id="az_1",
            session_id="oc_1",
            tool_call_id="c1",
            tool_name="events",
            arguments={},
            extra={"auth_card_message_id": "om_auth"},
        )

        await remove_authorization_card(SimpleNamespace(client=client), authorization)

        assert client.calls == [("patch", "om_auth")]
        assert "授权已完成" in str(client.patches[0][1])

    asyncio.run(run())


def test_remove_authorization_card_recalls_when_patch_fails() -> None:
    async def run() -> None:
        client = _CleanupClient(fail_patch=True)
        authorization = PendingAuthorization(
            authorization_id="az_1",
            session_id="oc_1",
            tool_call_id="c1",
            tool_name="events",
            arguments={},
            extra={"auth_card_message_id": "om_auth"},
        )

        await remove_authorization_card(SimpleNamespace(client=client), authorization)

        assert client.calls == [("patch", "om_auth"), ("recall", "om_auth")]

    asyncio.run(run())


class _CleanupClient:
    def __init__(self, *, fail_patch: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.patches: list[tuple[str, dict[str, Any]]] = []
        self.recalls: list[str] = []
        self.fail_patch = fail_patch

        class _IM:
            async def patch(_self, message_id: str, content: dict[str, Any]) -> dict[str, str]:
                self.calls.append(("patch", message_id))
                self.patches.append((message_id, content))
                if self.fail_patch:
                    raise RuntimeError("patch failed")
                return {"message_id": message_id}

            async def recall(_self, message_id: str) -> dict[str, str]:
                self.calls.append(("recall", message_id))
                self.recalls.append(message_id)
                return {}

        self.im = _IM()
