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

import builtins  # 'list' is a method here; annotations use builtins.list to avoid shadowing
from collections.abc import Iterable
from typing import Any

from chanfig import NestedDict

from .._namespace import Namespace
from .._url import quote_segment


class TasksNamespace(Namespace):
    r"""
    任务（Task）接口命名空间。

    通过 `client.task.tasks` 访问，封装飞书任务 v2 中任务对象的增删改查。任务以 `task_guid` 唯一标识，
    返回体中同时带有面向用户的 `task_id`（形如 `t100041`）与可直接打开的 `url`。

    通常无需直接实例化，应通过 `client.task.tasks` 访问。

    飞书文档:
        [创建任务](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/task-v2/task/create)
    """

    async def create(self, task: dict[str, Any], *, user_id_type: str | None = None) -> NestedDict:
        r"""
        创建任务。

        将 `task` 作为请求体发送至创建任务接口。

        Args:
            task: 任务数据，原样作为 JSON 发送，常见键包括 `summary`（标题）、`description`、`due`（截止时间）、
                `members`（成员，含 `id`/`role`/`type`）、`start`、`tasklists` 等。
            user_id_type: 用户 ID 的类型，如 `open_id`、`union_id`、`user_id`；为空时使用接口默认值。

        Returns:
            创建结果数据，含 `task` 字段，内含 `guid`（任务唯一标识）、`task_id`、`summary`、`status`、
            `url`、`creator`、`created_at` 等信息。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [创建任务](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/task-v2/task/create)

        Examples:
            >>> await client.task.tasks.create({"summary": "写周报"})  # doctest:+SKIP
            {'task': {'guid': 'd116...', 'task_id': 't100041', 'summary': '写周报', 'status': 'todo'}}
        """
        params: dict[str, Any] = {}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        return await self._request_data("POST", "task/v2/tasks", params=params, json=task)

    async def delete(self, task_guid: str) -> NestedDict:
        r"""
        删除任务。

        Args:
            task_guid: 任务唯一标识 `guid`。

        Returns:
            空数据体（接口成功时不返回额外字段）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [删除任务](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/task-v2/task/delete)

        Examples:
            >>> await client.task.tasks.delete("d116...")  # doctest:+SKIP
            {}
        """
        return await self._request_data("DELETE", f"task/v2/tasks/{quote_segment(task_guid)}")

    async def get(self, task_guid: str, *, user_id_type: str | None = None) -> NestedDict:
        r"""
        获取任务详情。

        Args:
            task_guid: 任务唯一标识 `guid`。
            user_id_type: 用户 ID 的类型，如 `open_id`、`union_id`、`user_id`；为空时使用接口默认值。

        Returns:
            任务数据，含 `task` 字段（结构同 [`create`][feishu.task.tasks.TasksNamespace.create] 的返回）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取任务详情](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/task-v2/task/get)

        Examples:
            >>> await client.task.tasks.get("d116...")  # doctest:+SKIP
            {'task': {'guid': 'd116...', 'summary': '写周报', 'status': 'todo'}}
        """
        params: dict[str, Any] = {}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        return await self._request_data("GET", f"task/v2/tasks/{quote_segment(task_guid)}", params=params)

    async def list(
        self,
        *,
        completed: bool | None = None,
        user_id_type: str | None = None,
        page_size: int = 50,
        max_items: int | None = None,
    ) -> builtins.list[NestedDict]:
        r"""
        获取“我负责的任务”列表。

        自动翻页并汇总当前用户负责的任务。该接口仅支持以 `user_access_token` 调用。

        Args:
            completed: 是否只返回已完成（`True`）/ 未完成（`False`）的任务；为空时返回全部。
            user_id_type: 用户 ID 的类型，如 `open_id`、`union_id`、`user_id`；为空时使用接口默认值。
            page_size: 每页数量。默认为 50；超过 [feishu.consts.MAX_PAGE_SIZE][] 时由客户端收敛。
            max_items: 最多返回的任务数量，`None` 表示不限制。默认为 `None`。

        Returns:
            任务对象列表（`data.items`）；无任务时返回空列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取任务列表](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/task-v2/task/list)

        Examples:
            >>> await client.as_user("u-xxx").task.tasks.list(completed=False)  # doctest:+SKIP
            [{'guid': 'd116...', 'summary': '写周报', 'status': 'todo'}]
        """
        params: dict[str, Any] = {}
        if completed is not None:
            params["completed"] = completed
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        return await self._client.paginate_get("task/v2/tasks", params=params, page_size=page_size, max_items=max_items)

    async def update(
        self,
        task_guid: str,
        task: dict[str, Any],
        update_fields: Iterable[str],
        *,
        user_id_type: str | None = None,
    ) -> NestedDict:
        r"""
        更新任务。

        飞书任务更新采用“字段白名单”语义：`task` 携带新值，`update_fields` 显式列出本次要更新的字段名，
        未列出的字段保持不变。

        Args:
            task_guid: 任务唯一标识 `guid`。
            task: 携带新值的任务字段，原样作为 JSON 发送（键同 `create`）。
            update_fields: 本次需要更新的字段名集合，例如 `["summary", "due"]`。
            user_id_type: 用户 ID 的类型，如 `open_id`、`union_id`、`user_id`；为空时使用接口默认值。

        Returns:
            更新后的任务数据，含 `task` 字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [更新任务](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/task-v2/task/patch)

        Examples:
            >>> await client.task.tasks.update("d116...", {"summary": "写月报"}, ["summary"])  # doctest:+SKIP
            {'task': {'guid': 'd116...', 'summary': '写月报'}}
        """
        params: dict[str, Any] = {}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        body = {"task": task, "update_fields": list(update_fields)}
        return await self._request_data("PATCH", f"task/v2/tasks/{quote_segment(task_guid)}", params=params, json=body)
