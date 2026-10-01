from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

_SESSION_SEPARATOR = "::"


class AgentRuntime(Protocol):
    """The event-handling surface shared by a single engine and a profile router."""

    async def run(self, event: Any) -> None: ...

    async def handle_card_action(self, event: Any) -> dict[str, Any]: ...

    async def resume_authorization(self, authorization_id: str, *, user: Mapping[str, Any] | None = None) -> str: ...


@dataclass(frozen=True)
class AgentProfile:
    """A named agent profile selected by one or more Feishu chats."""

    profile_id: str
    chat_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.profile_id.strip() or _SESSION_SEPARATOR in self.profile_id:
            raise ValueError("profile id must be non-empty and cannot contain '::'")


class ProfileRouter:
    """Resolve a stable profile from an inbound Feishu chat or persisted session key."""

    def __init__(self, *, profiles: Sequence[AgentProfile], default_profile: str) -> None:
        self._profiles = {profile.profile_id: profile for profile in profiles}
        if default_profile not in self._profiles:
            raise ValueError(f"default profile {default_profile!r} is not configured")
        self.default_profile = default_profile
        self._by_chat: dict[str, str] = {}
        for profile in profiles:
            for chat_id in profile.chat_ids:
                existing = self._by_chat.setdefault(chat_id, profile.profile_id)
                if existing != profile.profile_id:
                    raise ValueError(f"chat {chat_id!r} is assigned to multiple profiles")

    def profile_for_event(self, event: Any) -> str:
        return self._by_chat.get(_chat_id(event), self.default_profile)

    def profile_for_session(self, session_id: str) -> str:
        profile_id, separator, _rest = str(session_id).partition(_SESSION_SEPARATOR)
        return profile_id if separator and profile_id in self._profiles else self.default_profile


class ProfiledAgent:
    """Dispatch events to profile-specific engines while preserving pending-action ownership."""

    def __init__(self, engines: Mapping[str, Any], router: ProfileRouter) -> None:
        if set(engines) != set(router._profiles):
            raise ValueError("profile engines must exactly match configured profiles")
        for profile_id, engine in engines.items():
            namespace = getattr(engine, "session_namespace", None)
            if namespace is not None and namespace != profile_id:
                raise ValueError(f"profile {profile_id!r} must use session namespace {profile_id!r}")
        self._engines = dict(engines)
        self.router = router

    async def run(self, event: Any) -> None:
        await self._engines[self.router.profile_for_event(event)].run(event)

    async def handle_card_action(self, event: Any) -> dict[str, Any]:
        engine = await self._engine_for_pending(event, "__approval__", "approvals")
        return await engine.handle_card_action(event)

    async def resume_authorization(self, authorization_id: str, *, user: Mapping[str, Any] | None = None) -> str:
        engine = await self._engine_for_authorization(authorization_id)
        return await engine.resume_authorization(authorization_id, user=user)

    async def _finalize(self, event: Any, text: str) -> None:
        """Keep framework-level denials and errors in the originating profile's chat."""
        await self._engines[self.router.profile_for_event(event)]._finalize(event, text)

    async def _engine_for_pending(self, event: Any, value_key: str, store_name: str) -> Any:
        body = getattr(event, "body", None) or {}
        value = ((body.get("action") or {}).get("value") or {}) if isinstance(body, Mapping) else {}
        pending_id = value.get(value_key) if isinstance(value, Mapping) else None
        if pending_id:
            for engine in self._engines.values():
                pending = await getattr(engine, store_name).get(str(pending_id))
                if pending is not None:
                    return self._engines[self.router.profile_for_session(pending.session_id)]
        return self._engines[self.router.profile_for_event(event)]

    async def _engine_for_authorization(self, authorization_id: str) -> Any:
        for engine in self._engines.values():
            pending = await engine.authorizations.get(authorization_id)
            if pending is not None:
                return self._engines[self.router.profile_for_session(pending.session_id)]
        return self._engines[self.router.default_profile]


def namespaced_session_id(profile_id: str, session_id: str) -> str:
    return f"{profile_id}{_SESSION_SEPARATOR}{session_id}"


def _chat_id(event: Any) -> str:
    body = getattr(event, "body", None) or {}
    if not isinstance(body, Mapping):
        return ""
    message = body.get("message") or {}
    context = body.get("context") or {}
    if isinstance(message, Mapping) and message.get("chat_id"):
        return str(message["chat_id"])
    if isinstance(context, Mapping) and context.get("open_chat_id"):
        return str(context["open_chat_id"])
    return ""
