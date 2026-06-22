import pytest

from tests.conftest import envelope


@pytest.fixture
async def definitions(client_factory, recorder):
    client = client_factory(recorder=recorder, responder=lambda r: envelope({"approval_name": "请假"}))
    try:
        yield client.approval.definitions
    finally:
        await client.aclose()


class TestGet:
    async def test_returns_definition(self, definitions, recorder):
        resp = await definitions.get("ABC123")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("/approval/v4/approvals/ABC123")
        assert resp["approval_name"] == "请假"

    async def test_omits_unset_params(self, definitions, recorder):
        await definitions.get("ABC123")
        _, _, params, _ = recorder.last
        assert "locale" not in params and "user_id" not in params

    async def test_forwards_optional_params(self, definitions, recorder):
        await definitions.get("ABC123", locale="zh-CN", user_id="u1")
        _, _, params, _ = recorder.last
        assert params["locale"] == "zh-CN"
        assert params["user_id"] == "u1"
