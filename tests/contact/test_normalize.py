import pytest
from chanfig import NestedDict

from feishu.contact.normalize import (
    get_user_department_ids,
    get_user_email,
    get_user_identity,
    is_active_user,
    normalize_department,
    normalize_user,
)

RAW = NestedDict(
    {
        "user_id": "u1",
        "open_id": "ou_1",
        "union_id": "on_1",
        "name": "Alice",
        "email": "alice@corp.com",
        "enterprise_email": "alice@ent.com",
        "department_ids": ["od-1", "od-2"],
        "status": {"is_activated": True, "is_frozen": False, "is_resigned": False},
    }
)


class TestNormalizeUser:
    def test_shape(self):
        u = normalize_user(RAW)
        assert u["user_id"] == "u1" and u["open_id"] == "ou_1" and u["union_id"] == "on_1"
        assert u["name"] == "Alice" and u["department_ids"] == ["od-1", "od-2"]
        assert u["active"] is True

    def test_raw_roundtrips(self):
        # the original payload round-trips under "raw" so callers can reach un-normalized fields
        assert normalize_user(RAW)["raw"]["enterprise_email"] == "alice@ent.com"


class TestGetUserEmail:
    @pytest.mark.parametrize(
        "prefer_enterprise, expected",
        [(True, "alice@ent.com"), (False, "alice@corp.com")],
    )
    def test_prefers_enterprise(self, prefer_enterprise, expected):
        assert get_user_email(RAW, prefer_enterprise=prefer_enterprise) == expected


class TestIsActiveUser:
    @pytest.mark.parametrize(
        "status, expected",
        [
            ({"is_activated": True, "is_frozen": False, "is_resigned": False}, True),
            ({"is_activated": True, "is_frozen": True, "is_resigned": False}, False),
        ],
    )
    def test_active(self, status, expected):
        assert is_active_user(NestedDict({"status": status})) is expected


class TestGetUserDepartmentIds:
    def test_default_empty(self):
        assert get_user_department_ids(NestedDict({})) == []


class TestGetUserIdentity:
    def test_extracts_ids(self):
        identity = get_user_identity(RAW)
        assert identity["user_id"] == "u1"
        assert identity["open_id"] == "ou_1"
        assert identity["union_id"] == "on_1"

    def test_missing_ids_are_none(self):
        # Callers can index every id field without a KeyError, even when absent.
        identity = get_user_identity(NestedDict({"user_id": "u1"}))
        assert identity["user_id"] == "u1"
        assert identity["open_id"] is None and identity["union_id"] is None


class TestNormalizeDepartment:
    def test_core_fields_and_raw(self):
        raw = NestedDict(
            {
                "department_id": "d1",
                "open_department_id": "od_1",
                "parent_department_id": "od_0",
                "name": "Engineering",
                "member_count": 42,
            }
        )
        dept = normalize_department(raw)
        assert dept["department_id"] == "d1"
        assert dept["open_department_id"] == "od_1"
        assert dept["parent_department_id"] == "od_0"
        assert dept["name"] == "Engineering"
        assert dept["member_count"] == 42
        # the original payload round-trips under "raw"
        assert dept["raw"]["member_count"] == 42

    def test_sparse_defaults_to_none(self):
        # Callers can index every field without a KeyError, even when sparse.
        dept = normalize_department(NestedDict({"department_id": "d1", "name": "Solo"}))
        assert dept["department_id"] == "d1" and dept["name"] == "Solo"
        assert dept["open_department_id"] is None
        assert dept["parent_department_id"] is None
        assert dept["member_count"] is None
