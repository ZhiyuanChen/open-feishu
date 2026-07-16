from __future__ import annotations

import pytest

from feishu.contact.directory import fetch_organization_snapshot


class _Departments:
    async def list(self, department_id: str, **kwargs):
        return [
            {"open_department_id": "od-research"},
            {"open_department_id": "od-platform"},
        ]


class _Users:
    async def list(self, department_id: str, **kwargs):
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
async def test_fetches_directory() -> None:
    client = _Client()

    snapshot = await fetch_organization_snapshot(client)

    assert [department["open_department_id"] for department in snapshot.departments] == [
        "od-research",
        "od-platform",
    ]
    assert [user["user_id"] for user in snapshot.users] == ["u-alice", "u-bob", "u-cara"]


@pytest.mark.asyncio
async def test_deduplicates_users() -> None:
    class _DuplicateUsers:
        async def list(self, department_id: str, **kwargs):
            return {
                "0": [
                    {
                        "user_id": "u-alice",
                        "open_id": "ou-alice",
                    }
                ],
                "od-platform": [
                    {
                        "user_id": "u-alice",
                        "open_id": "ou-alice",
                    },
                    {"user_id": "u-bob", "open_id": "ou-alice"},
                ],
                "od-research": [],
            }[department_id]

    client = _Client()
    client.contact.users = _DuplicateUsers()

    snapshot = await fetch_organization_snapshot(client)

    assert [user["user_id"] for user in snapshot.users] == ["u-alice", "u-bob"]
