import pytest

from tests.conftest import envelope


@pytest.fixture
async def employees(client_factory, recorder):
    """Bound directory employees namespace over a recorder; default responder returns an empty envelope."""

    clients = []

    def factory(responder=lambda r: envelope({})):
        client = client_factory(recorder=recorder, responder=responder)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()


class TestMgetEmployees:
    async def test_mget_posts_ids_and_required_fields(self, employees, recorder):
        employee = {
            "base_info": {
                "employee_id": "u1",
                "custom_field_values": [{"field_key": "C-alias", "text_value": {"default_value": "alice"}}],
            }
        }
        client = employees(lambda r: envelope({"employees": [employee], "abnormals": []}))
        data = await client.directory.employees.mget(
            ["u1"],
            required_fields=["base_info.employee_id", "base_info.custom_field_values"],
            employee_id_type="user_id",
        )
        method, path, params, body = recorder.last
        assert method == "POST" and path.endswith("/directory/v1/employees/mget")
        assert params["employee_id_type"] == "user_id"
        assert "department_id_type" not in params
        assert body["employee_ids"] == ["u1"]
        assert body["required_fields"] == [
            "base_info.employee_id",
            "base_info.custom_field_values",
        ]
        assert data["employees"][0]["base_info"]["employee_id"] == "u1"

    async def test_mget_forwards_id_types(self, employees, recorder):
        client = employees(lambda r: envelope({"employees": []}))
        await client.directory.employees.mget(
            ["u1"],
            required_fields=["base_info.employee_id"],
            employee_id_type="employee_id",
            department_id_type="open_department_id",
        )
        _, _, params, _ = recorder.last
        assert params["employee_id_type"] == "employee_id"
        assert params["department_id_type"] == "open_department_id"

    async def test_mget_rejects_over_100(self, employees, recorder):
        client = employees(lambda r: envelope({"employees": []}))
        with pytest.raises(ValueError, match="100"):
            await client.directory.employees.mget(
                [f"u{i}" for i in range(101)],
                required_fields=["base_info.employee_id"],
            )
        assert len(recorder) == 0
