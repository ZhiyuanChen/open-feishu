from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from feishu.agent.context import ToolContext, use_tool_context
from feishu.agent.oauth import (
    _RESUME_TASKS,
    build_authorize_url_builder,
    oauth_callback_handler,
    remove_authorization_card,
)
from feishu.agent.session import PendingAuthorization
from feishu.auth import OAuthStateSigner, UserTokenProvider


@pytest.mark.parametrize("missing", [False, True])
async def test_scheduled_expiry(expired_scheduled_wait, missing):
    from feishu.agent.oauth import resume_authorization

    agent, store, run, _event, writes = await expired_scheduled_wait("authorization", missing)
    await resume_authorization(
        agent, "authorization", user={"user_id": "owner", "tenant_key": "tenant"}, propagate_errors=True
    )
    assert writes == []
    assert store.get_run(run.run_id).status == ("unknown" if missing else "failed")


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


def test_authorize_state_binds_tenant() -> None:
    signer = OAuthStateSigner("secret")
    builder = build_authorize_url_builder(cast(UserTokenProvider, _Provider()), signer, "https://example.invalid")
    url = builder({"user_id": "same"}, (), SimpleNamespace(authorization_id="az", tenant_key="tenant-a"))
    assert url is not None
    state = signer.consume(parse_qs(urlsplit(url).query)["state"][0])
    assert state is not None
    assert state.tenant_key == "tenant-a"


def test_legacy_state_uses_fixed_tenant() -> None:
    signer = OAuthStateSigner("secret")
    builder = build_authorize_url_builder(cast(UserTokenProvider, _Provider()), signer, "https://example.invalid")
    context = ToolContext(event=SimpleNamespace(), trusted_tenant_key="tenant-a")
    with use_tool_context(context):
        url = builder({"user_id": "same"}, (), SimpleNamespace(authorization_id="az"))
    assert url is not None
    state = signer.consume(parse_qs(urlsplit(url).query)["state"][0])
    assert state is not None
    assert state.tenant_key == "tenant-a"


@pytest.mark.parametrize(
    "tenant,user_id,status",
    [("tenant-a", "same", 200), ("tenant-b", "same", 403), (None, "same", 403), ("tenant-a", "other", 403)],
)
async def test_callback_requires_tenant(tenant, user_id, status, monkeypatch) -> None:
    provider = _Provider()
    saved = []

    async def user_info(token):
        return {"user_id": user_id, "tenant_key": tenant}

    async def save_for_tenant(tenant_key, token_data, *, user_info):
        saved.append(tenant_key)

    provider.client.oauth.user_info = user_info
    monkeypatch.setattr(provider.store, "save_for_tenant", save_for_tenant, raising=False)
    signer = OAuthStateSigner("secret")
    state = signer.issue(user_keys=("user_id:same",), tenant_key="tenant-a")
    handler = oauth_callback_handler(cast(UserTokenProvider, provider), signer, "https://example.invalid")
    response = await handler(SimpleNamespace(query_params={"code": "code", "state": state}))
    assert response.status_code == status
    assert saved == (["tenant-a"] if status == 200 else [])
    assert provider.store.saved == []


async def test_scoped_callback_no_fallback() -> None:
    provider = _Provider()

    async def user_info(token):
        return {"user_id": "same", "tenant_key": "tenant-a"}

    provider.client.oauth.user_info = user_info
    signer = OAuthStateSigner("secret")
    handler = oauth_callback_handler(cast(UserTokenProvider, provider), signer, "https://example.invalid")
    for tenant, expected in (("tenant-a", 503), (None, 403)):
        state = signer.issue(user_keys=("user_id:same",), tenant_key=tenant)
        response = await handler(SimpleNamespace(query_params={"code": "code", "state": state}))
        assert response.status_code == expected
    assert provider.store.saved == []


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


async def test_oauth_callback_registers_resume_task_with_agent_runtime() -> None:
    provider = _Provider()
    signer = OAuthStateSigner("secret")
    state = signer.issue(user_keys=("open_id:ou_1",), extra={"authorization_id": "az_1"})
    request = SimpleNamespace(query_params={"code": "code_1", "state": state})
    tracked: set[asyncio.Task[Any]] = set()
    release = asyncio.Event()

    async def on_authorized(_authorization_id: str, *, user: dict[str, Any]) -> str:
        await release.wait()
        return str(user["open_id"])

    handler = oauth_callback_handler(
        cast(UserTokenProvider, provider),
        signer,
        "https://agent.example/oauth/callback",
        on_authorized=on_authorized,
        track_task=tracked.add,
    )
    await handler(request)
    await asyncio.sleep(0)

    assert len(tracked) == 1
    assert not _RESUME_TASKS
    release.set()
    await asyncio.gather(*tracked)
    assert all(task.done() for task in tracked)


@pytest.mark.parametrize(("fail_patch", "recalls"), ((False, []), (True, ["om_auth"])))
async def test_finishes_authorization_card(fail_patch: bool, recalls: list[str]) -> None:
    client = _CleanupClient(fail_patch=fail_patch)
    authorization = PendingAuthorization(
        authorization_id="az_1",
        session_id="oc_1",
        tool_call_id="c1",
        tool_name="events",
        arguments={},
        extra={"auth_card_message_id": "om_auth"},
    )

    await remove_authorization_card(SimpleNamespace(client=client), authorization)

    assert [message_id for message_id, _ in client.patches] == ["om_auth"]
    assert client.recalls == recalls


class _CleanupClient:
    def __init__(self, *, fail_patch: bool = False) -> None:
        self.patches: list[tuple[str, dict[str, Any]]] = []
        self.recalls: list[str] = []
        self.fail_patch = fail_patch

        class _IM:
            async def patch(_self, message_id: str, content: dict[str, Any]) -> dict[str, str]:
                self.patches.append((message_id, content))
                if self.fail_patch:
                    raise RuntimeError("patch failed")
                return {"message_id": message_id}

            async def recall(_self, message_id: str) -> dict[str, str]:
                self.recalls.append(message_id)
                return {}

        self.im = _IM()
