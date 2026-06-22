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

from typing import Any, TypedDict


class NormalizedUser(TypedDict):
    user_id: str
    open_id: str
    union_id: str
    name: str
    email: str | None
    department_ids: list[str]
    status: dict[str, Any]
    active: bool
    raw: Any


class NormalizedDepartment(TypedDict):
    department_id: str
    open_department_id: str
    parent_department_id: str
    name: str
    member_count: int
    raw: Any
