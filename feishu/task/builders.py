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

from typing import Any

from chanfig import NestedDict


def task_payload(
    summary: str,
    *,
    description: str | None = None,
    due: dict[str, Any] | None = None,
    start: dict[str, Any] | None = None,
    members: list[dict[str, Any]] | None = None,
    tasklists: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> NestedDict:
    r"""
    Build a Feishu task creation payload.

    Args:
        summary: Task title.
        description: Optional task description.
        due: Optional Feishu `due` object. Passed through unchanged.
        start: Optional Feishu `start` object. Passed through unchanged.
        members: Optional Feishu member objects.
        tasklists: Optional Feishu tasklist objects.
        extra: Additional Feishu task fields to include when not `None`.

    Returns:
        Task payload for [feishu.task.tasks.TasksNamespace.create][].

    Examples:
        >>> task_payload("写周报", description="周五前完成").summary
        '写周报'
    """
    payload = NestedDict(summary=summary)
    if description:
        payload.description = description
    if due:
        payload.due = NestedDict(due)
    if start:
        payload.start = NestedDict(start)
    if members:
        payload.members = [NestedDict(item) for item in members]
    if tasklists:
        payload.tasklists = [NestedDict(item) for item in tasklists]
    for key, value in extra.items():
        if value is not None:
            payload[key] = value
    return payload
