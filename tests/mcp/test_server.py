import base64
import sys
import types

import pytest

from feishu.mcp.server import _decode_base64_data, create_server


class _FakeFastMCP:
    def __init__(self, name, *, instructions):
        self.name = name
        self.instructions = instructions
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class _FakeClient:
    def __init__(self):
        self.created_events = []
        self.created_approval_instances = []
        self.as_user_tokens = []

        outer = self

        class _IM:
            async def get_resource(_self, *args, **kwargs):
                raise AssertionError("resource download must require a user token before calling the client")

        class _Events:
            async def create(_self, calendar_id, event, **kwargs):
                self.created_events.append((calendar_id, event, kwargs))
                return {"event": {"event_id": "evt_1"}}

        class _Calendar:
            events = _Events()

        class _OAuth:
            async def user_info(_self, token):
                outer.user_info_token = token
                return {"open_id": "ou_self", "user_id": "u_self"}

        class _Instances:
            async def create(_self, instance):
                outer.created_approval_instances.append(instance)
                return {"instance_code": "approval_1"}

        class _Approval:
            instances = _Instances()

        self.im = _IM()
        self.calendar = _Calendar()
        self.oauth = _OAuth()
        self.approval = _Approval()

    def as_user(self, token):
        self.as_user_tokens.append(token)
        return self


@pytest.fixture
def mcp_server(monkeypatch):
    fastmcp = types.ModuleType("mcp.server.fastmcp")
    fastmcp.FastMCP = _FakeFastMCP
    server_mod = types.ModuleType("mcp.server")
    mcp_mod = types.ModuleType("mcp")
    monkeypatch.setitem(sys.modules, "mcp", mcp_mod)
    monkeypatch.setitem(sys.modules, "mcp.server", server_mod)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp)
    client = _FakeClient()
    server = create_server(client=client)
    server.client = client
    return server


async def test_write_tool_requires_explicit_confirmation(mcp_server):
    tool = mcp_server.tools["feishu_create_calendar_event"]

    with pytest.raises(ValueError, match="confirmation"):
        await tool(
            "cal_1", summary="Ship", start_time="2026-06-30T10:00:00+08:00", end_time="2026-06-30T11:00:00+08:00"
        )

    result = await tool(
        "cal_1",
        summary="Ship",
        start_time="2026-06-30T10:00:00+08:00",
        end_time="2026-06-30T11:00:00+08:00",
        confirmed=True,
    )
    assert result["event"]["event_id"] == "evt_1"


async def test_private_message_resource_requires_user_access_token(mcp_server):
    tool = mcp_server.tools["feishu_message_pdf_to_text"]

    with pytest.raises(ValueError, match="user_access_token"):
        await tool("om_1", "file_1")


async def test_private_tools_do_not_fall_back_to_process_user_token(mcp_server, monkeypatch):
    monkeypatch.setenv("FEISHU_USER_ACCESS_TOKEN", "global-user-token")
    tool = mcp_server.tools["feishu_list_drive_files"]

    with pytest.raises(ValueError, match="user_access_token"):
        await tool()

    assert mcp_server.client.as_user_tokens == []


async def test_create_approval_instance_derives_applicant_from_user_token(mcp_server):
    tool = mcp_server.tools["feishu_create_approval_instance"]

    with pytest.raises(ValueError, match="user_access_token"):
        await tool("APPROVAL", {}, confirmed=True)

    result = await tool(
        "APPROVAL",
        {"reason": "ship it"},
        department_id="dept_1",
        user_access_token="u-current",
        confirmed=True,
    )

    assert result == {"instance_code": "approval_1"}
    assert mcp_server.client.user_info_token == "u-current"
    payload = mcp_server.client.created_approval_instances[0]
    assert payload["approval_code"] == "APPROVAL"
    assert payload["user_id"] == "u_self"
    assert payload["open_id"] == "ou_self"
    assert payload["department_id"] == "dept_1"
    assert "victim" not in repr(payload)


def test_base64_decode_enforces_size_limit():
    payload = base64.b64encode(b"abcdef").decode("ascii")

    with pytest.raises(ValueError, match="too large"):
        _decode_base64_data(payload, max_bytes=3)
