# OpenFeishu
# Copyright (C) 2024-Present  DanLing

# This file is part of OpenFeishu.

# OpenFeishu is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.

# OpenFeishu is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# For additional terms and clarifications, please refer to our License FAQ at:
# <https://multimolecule.danling.org/about/license-faq>.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class OrganizationSnapshot:
    r"""Raw organization-directory facts returned by Feishu.

    ``departments`` contains the descendants returned from the root department;
    ``users`` contains one raw user record for each stable Feishu identity found
    across the root and descendant departments.  The SDK neither filters nor
    derives account information from email addresses.
    """

    departments: tuple[Mapping[str, Any], ...]
    users: tuple[Mapping[str, Any], ...]


def _department_id(department: Mapping[str, Any]) -> str | None:
    return department.get("open_department_id") or department.get("department_id")


def _user_identity(user: Mapping[str, Any]) -> tuple[str, str] | None:
    for field in ("user_id", "open_id", "union_id"):
        value = user.get(field)
        if value:
            return field, str(value)
    return None


async def fetch_organization_snapshot(
    client: Any,
    *,
    root_department_id: str = "0",
    page_size: int = 50,
    max_items: int | None = None,
) -> OrganizationSnapshot:
    r"""Fetch raw departments and de-duplicated user facts for an organization.

    The root department's descendants are fetched once through
    ``client.contact.departments.list``.  Users are then listed for the root and
    every discovered department.  ``user_id`` is requested and used as the
    canonical stable identity; ``open_id`` and ``union_id`` are only fallbacks
    when an API response lacks ``user_id``.

    Args:
        client: A Feishu client exposing the existing contact namespaces.
        root_department_id: Root department ID. Defaults to the Feishu root,
            ``"0"``.
        page_size: Page size passed to the existing contact APIs.
        max_items: Optional maximum passed to the existing contact APIs.

    Returns:
        An [OrganizationSnapshot][] containing raw Feishu department and user
        facts. User records are ordered by their stable Feishu identity.
    """
    departments = tuple(
        await client.contact.departments.list(
            root_department_id,
            department_id_type="open_department_id",
            fetch_child=True,
            page_size=page_size,
            max_items=max_items,
        )
    )
    department_ids = sorted(
        {department_id for department in departments if (department_id := _department_id(department)) is not None}
    )

    users_by_identity: dict[tuple[str, str], Mapping[str, Any]] = {}
    for department_id in (root_department_id, *department_ids):
        users = await client.contact.users.list(
            department_id,
            user_id_type="user_id",
            department_id_type="open_department_id",
            page_size=page_size,
            max_items=max_items,
        )
        for user in users:
            identity = _user_identity(user)
            if identity is not None:
                users_by_identity.setdefault(identity, user)

    return OrganizationSnapshot(
        departments=departments,
        users=tuple(users_by_identity[identity] for identity in sorted(users_by_identity)),
    )
