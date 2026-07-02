from __future__ import annotations

import pytest

from feishu.agent.bundles import (
    BUNDLES,
    CALENDAR_SCOPES,
    MAIL_READ_SCOPES,
    MAIL_SEND_SCOPES,
    BundleContext,
    build_tool_registry,
)
from feishu.agent.context import ToolContext, use_tool_context
from feishu.agent.result import ToolOutcome
from feishu.agent.toolkit import list_mail_messages, reauth_on_permission_error
from feishu.agent.tools import Tool, ToolRegistry
from feishu.errors import FeishuPermissionError


async def test_bundles_registry_builds_dispatchable_tools_from_register_method() -> None:
    class ExampleBundle:
        def register(self, registry: ToolRegistry, context: BundleContext) -> None:
            registry.add(
                Tool(
                    name="example_tool",
                    description=f"example {context.locale}",
                    input_schema={
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                        "additionalProperties": False,
                    },
                    handler=lambda city: {"ok": True, "city": city, "locale": context.locale},
                )
            )

    BUNDLES.register(ExampleBundle, name="test.example", override=True)
    registry = build_tool_registry(["test.example"], BundleContext(locale="zh-CN"))
    result = await registry.dispatch("example_tool", {"city": "Shanghai"})

    assert registry.get("example_tool").description == "example zh-CN"
    assert result.outcome is ToolOutcome.COMPLETED
    assert result.content == {"ok": True, "city": "Shanghai", "locale": "zh-CN"}


def test_workplace_bundle_builds_default_tools_with_scopes() -> None:
    registry = build_tool_registry(["feishu.workplace"], BundleContext())

    assert registry.get("list_calendar_events").auth_scopes == CALENDAR_SCOPES
    assert registry.get("list_mail_messages").auth_scopes == MAIL_READ_SCOPES
    assert registry.get("search_mail_messages").auth_scopes == MAIL_READ_SCOPES
    assert registry.get("summarize_mail_message").auth_scopes == MAIL_READ_SCOPES
    assert registry.get("send_mail_message").auth_scopes == MAIL_SEND_SCOPES
    assert registry.get("send_mail_message").requires_approval is True


def test_workplace_mail_tools_pin_current_user_mailbox() -> None:
    registry = build_tool_registry(["feishu.workplace"], BundleContext())

    for name in (
        "list_mail_messages",
        "search_mail_messages",
        "get_mail_message",
        "summarize_mail_message",
        "summarize_mail_messages",
        "list_mail_folders",
        "send_mail_message",
    ):
        assert "user_mailbox_id" not in registry.get(name).input_schema["properties"]


def test_build_tool_registry_rejects_duplicate_tool_names() -> None:
    class FirstBundle:
        def register(self, registry: ToolRegistry, context: BundleContext) -> None:
            registry.add(Tool(name="same", description="one", input_schema={"type": "object"}, handler=lambda: None))

    class SecondBundle:
        def register(self, registry: ToolRegistry, context: BundleContext) -> None:
            registry.add(Tool(name="same", description="two", input_schema={"type": "object"}, handler=lambda: None))

    BUNDLES.register(FirstBundle, name="test.duplicate_first", override=True)
    BUNDLES.register(SecondBundle, name="test.duplicate_second", override=True)

    with pytest.raises(ValueError, match="tool 'same' is already registered"):
        build_tool_registry(["test.duplicate_first", "test.duplicate_second"], BundleContext())


def test_atomic_mail_factories_stay_in_singular_toolkit_package() -> None:
    tool = list_mail_messages(description="List mail messages", auth_scopes=MAIL_READ_SCOPES)

    assert tool.name == "list_mail_messages"
    assert tool.auth_scopes == MAIL_READ_SCOPES


async def test_workplace_mail_tool_requests_user_auth_without_user_token() -> None:
    registry = build_tool_registry(["feishu.workplace"], BundleContext())

    def authorize_url_builder(user, scopes):
        assert user == {"open_id": "ou_test"}
        assert scopes == MAIL_READ_SCOPES
        return "https://auth.example/mail"

    context = ToolContext(user={"open_id": "ou_test"}, authorize_url_builder=authorize_url_builder)
    with use_tool_context(context):
        result = await registry.dispatch("list_mail_messages", {})

    assert result.outcome is ToolOutcome.NEEDS_USER_AUTH
    assert result.authorize_url == "https://auth.example/mail"
    assert result.auth_scopes == MAIL_READ_SCOPES
    assert result.is_error is True


async def test_workplace_mail_write_tool_requests_user_auth_without_user_token() -> None:
    registry = build_tool_registry(["feishu.workplace"], BundleContext())

    def authorize_url_builder(user, scopes):
        assert user == {"open_id": "ou_test"}
        assert scopes == MAIL_SEND_SCOPES
        return "https://auth.example/mail-send"

    context = ToolContext(user={"open_id": "ou_test"}, authorize_url_builder=authorize_url_builder)
    with use_tool_context(context):
        result = await registry.dispatch(
            "send_mail_message",
            {"to": [{"mail_address": "alice@example.com"}], "subject": "Hello"},
        )

    assert result.outcome is ToolOutcome.NEEDS_USER_AUTH
    assert result.authorize_url == "https://auth.example/mail-send"
    assert result.auth_scopes == MAIL_SEND_SCOPES
    assert result.is_error is True


async def test_workplace_mail_write_tool_turns_permission_error_into_auth_handoff() -> None:
    class FakeMessages:
        async def send(self, user_mailbox_id, **kwargs):
            assert user_mailbox_id == "me"
            assert kwargs == {
                "subject": None,
                "to": None,
                "raw": "ZW1s",
                "cc": None,
                "bcc": None,
                "body_plain_text": None,
                "body_html": None,
                "attachments": [{"file_name": "hello.txt", "content": "aGVsbG8"}],
                "dedupe_key": None,
                "head_from": None,
            }
            raise FeishuPermissionError(
                99991679,
                {
                    "permission_violations": [
                        {"type": "action_privilege_required", "subject": "mail:user_mailbox.message:send"}
                    ]
                },
            )

    class FakeMail:
        messages = FakeMessages()

    class FakeUserClient:
        mail = FakeMail()

    class FakeUserTokens:
        async def as_user(self, user):
            assert user == {"open_id": "ou_test"}
            return FakeUserClient()

    registry = build_tool_registry(["feishu.workplace"], BundleContext())

    def authorize_url_builder(user, scopes):
        assert user == {"open_id": "ou_test"}
        assert scopes == MAIL_SEND_SCOPES
        return "https://auth.example/authorize-mail"

    context = ToolContext(
        user={"open_id": "ou_test"},
        user_tokens=FakeUserTokens(),
        authorize_url_builder=authorize_url_builder,
    )
    with use_tool_context(context):
        result = await registry.dispatch(
            "send_mail_message",
            {"raw": "ZW1s", "attachments": [{"file_name": "hello.txt", "content": "aGVsbG8"}]},
        )

    assert result.outcome is ToolOutcome.NEEDS_USER_AUTH
    assert result.authorize_url == "https://auth.example/authorize-mail"
    assert result.auth_scopes == MAIL_SEND_SCOPES
    assert result.is_error is True
    assert "mail:user_mailbox.message:send" in result.content


async def test_reauth_wrapper_turns_permission_error_into_auth_handoff() -> None:
    async def handler() -> None:
        raise FeishuPermissionError(
            99991679,
            "permission denied",
            raw={
                "permission_violations": [
                    {"type": "action_privilege_required", "subject": "calendar:calendar:readonly"}
                ]
            },
        )

    tool = Tool(
        name="create_calendar_event",
        description="create event",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
        requires_approval=True,
    )
    wrapped = reauth_on_permission_error(tool, CALENDAR_SCOPES)

    def authorize_url_builder(user, scopes):
        assert user == {"open_id": "ou_test"}
        assert scopes == CALENDAR_SCOPES
        return "https://auth.example/authorize"

    context = ToolContext(user={"open_id": "ou_test"}, authorize_url_builder=authorize_url_builder)
    with use_tool_context(context):
        result = await wrapped.handler()

    assert result.outcome is ToolOutcome.NEEDS_USER_AUTH
    assert result.authorize_url == "https://auth.example/authorize"
    assert result.auth_scopes == CALENDAR_SCOPES
    assert result.is_error is True
    assert "calendar:calendar:readonly" in result.content


def test_build_tool_registry_reports_unknown_bundle_names_clearly() -> None:
    with pytest.raises(ValueError, match=r"unknown bundle 'missing'; registered bundles: .*feishu\.workplace"):
        build_tool_registry(["missing"], BundleContext())
