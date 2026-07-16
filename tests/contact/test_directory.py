from __future__ import annotations

from typing import Any

import pytest

from feishu.contact.directory import fetch_organization_snapshot


class _Departments:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list(self, department_id: str, **kwargs):
        self.calls.append((department_id, kwargs))
        return [
            {"open_department_id": "od-research"},
            {"open_department_id": "od-platform"},
        ]


class _Users:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list(self, department_id: str, **kwargs):
        self.calls.append((department_id, kwargs))
        return {
            "0": [{"user_id": "u-alice", "name": "Alice"}],
            "od-platform": [
                {"user_id": "u-alice", "name": "Alice"},
                {"user_id": "u-bob", "name": "Bob"},
            ],
            "od-research": [{"user_id": "u-cara", "name": "Cara"}],
        }[department_id]


class _Client:
    def __init__(self) -> None:
        self.contact = type(
            "Contact",
            (),
            {"departments": _Departments(), "users": _Users()},
        )()


@pytest.mark.asyncio
async def test_fetch_organization_snapshot_discovers_departments_and_deduplicates_users() -> None:
    client = _Client()

    snapshot = await fetch_organization_snapshot(client)

    assert [department["open_department_id"] for department in snapshot.departments] == [
        "od-research",
        "od-platform",
    ]
    assert [user["user_id"] for user in snapshot.users] == ["u-alice", "u-bob", "u-cara"]
    assert client.contact.departments.calls == [
        (
            "0",
            {
                "department_id_type": "open_department_id",
                "fetch_child": True,
                "page_size": 50,
                "max_items": None,
            },
        )
    ]
    assert [call[0] for call in client.contact.users.calls] == ["0", "od-platform", "od-research"]
    assert all(call[1]["user_id_type"] == "user_id" for call in client.contact.users.calls)


@pytest.mark.asyncio
async def test_fetch_organization_snapshot_deduplicates_by_user_id_without_email_policy() -> None:
    class _DuplicateUsers:
        async def list(self, department_id: str, **kwargs):
            return {
                "0": [
                    {
                        "user_id": "u-alice",
                        "open_id": "ou-alice",
                        "email": "alice@outside.example",
                    }
                ],
                "od-platform": [
                    {
                        "user_id": "u-alice",
                        "open_id": "ou-alice",
                        "email": "alice@inside.example",
                    },
                    {"user_id": "u-bob", "open_id": "ou-alice", "email": "bob@outside.example"},
                ],
                "od-research": [],
            }[department_id]

    client = _Client()
    client.contact.users = _DuplicateUsers()

    snapshot = await fetch_organization_snapshot(client)

    assert [user["user_id"] for user in snapshot.users] == ["u-alice", "u-bob"]
    assert snapshot.users[0]["email"] == "alice@outside.example"
