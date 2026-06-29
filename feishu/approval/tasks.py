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

from .._namespace import Namespace


class TasksNamespace(Namespace):
    r"""
    审批任务接口命名空间。

    通过 `client.approval.tasks` 访问，封装飞书审批中审批任务（task）相关的服务端接口，
    包括同意、拒绝与转交审批任务等能力。审批任务隶属于某个审批实例（`instance_code`），
    以 `task_id` 标识，代表实例流程中某一节点上指定审批人的待办。

    通常无需直接实例化，应通过 `client.approval.tasks` 访问。

    飞书文档:
        [审批 / 审批任务](https://open.feishu.cn/document/server-docs/approval-v4/task/approve)
    """

    async def approve(self, task: dict[str, Any]) -> NestedDict:
        r"""
        同意审批任务。

        `task` 是描述待同意任务的请求体，原样作为 JSON 发送，常见键包括 `approval_code`、
        `instance_code`、`task_id`、`user_id`、`comment` 等。

        Args:
            task: 审批任务操作对象，例如
                `{"approval_code": "ABC123", "instance_code": "INST123",
                "task_id": "T1", "user_id": "u1", "comment": "同意"}`。

        Returns:
            接口返回的数据（通常为空）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [同意审批任务](https://open.feishu.cn/document/server-docs/approval-v4/task/approve)

        Examples:
            >>> await client.approval.tasks.approve({"task_id": "T1", "user_id": "u1"})  # doctest:+SKIP
            {}
        """
        return await self._request_data("POST", "approval/v4/tasks/approve", json=task)

    async def reject(self, task: dict[str, Any]) -> NestedDict:
        r"""
        拒绝审批任务。

        `task` 是描述待拒绝任务的请求体，原样作为 JSON 发送，常见键包括 `approval_code`、
        `instance_code`、`task_id`、`user_id`、`comment` 等。

        Args:
            task: 审批任务操作对象，例如
                `{"approval_code": "ABC123", "instance_code": "INST123",
                "task_id": "T1", "user_id": "u1", "comment": "拒绝"}`。

        Returns:
            接口返回的数据（通常为空）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [拒绝审批任务](https://open.feishu.cn/document/server-docs/approval-v4/task/reject)

        Examples:
            >>> await client.approval.tasks.reject({"task_id": "T1", "user_id": "u1"})  # doctest:+SKIP
            {}
        """
        return await self._request_data("POST", "approval/v4/tasks/reject", json=task)

    async def transfer(self, task: dict[str, Any]) -> NestedDict:
        r"""
        转交审批任务。

        `task` 是描述待转交任务的请求体，原样作为 JSON 发送，常见键包括 `approval_code`、
        `instance_code`、`task_id`、`user_id`、`transfer_user_id`、`comment` 等。

        Args:
            task: 审批任务转交对象，例如
                `{"approval_code": "ABC123", "instance_code": "INST123",
                "task_id": "T1", "user_id": "u1", "transfer_user_id": "u2"}`。

        Returns:
            接口返回的数据（通常为空）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [转交审批任务](https://open.feishu.cn/document/server-docs/approval-v4/task/transfer)

        Examples:
            >>> await client.approval.tasks.transfer({"task_id": "T1", "transfer_user_id": "u2"})  # doctest:+SKIP
            {}
        """
        return await self._request_data("POST", "approval/v4/tasks/transfer", json=task)

    async def query(
        self,
        user_id: str,
        *,
        topic: str = "1",
        user_id_type: str = "open_id",
        page_size: int = 50,
        max_items: int | None = None,
    ) -> list[NestedDict]:
        r"""
        查询某个用户的审批任务列表（默认待办）。

        自动翻页并将各页结果拼接为单个列表返回。`user_id` 为必填查询参数；`topic` 指定任务类型：
        `"1"` 待办、`"2"` 已办、`"3"` 发起、`"4"` 抄送，默认 `"1"`（待办）。任务条目位于响应体的
        `tasks` 字段下，每个条目为含 `id`（`task_id`）、`instance_code`、`approval_code`、`status`
        等字段的对象。

        Args:
            user_id: 目标用户标识（与 `user_id_type` 对应），必填。
            topic: 任务类型，`"1"`/`"2"`/`"3"`/`"4"`。默认为 `"1"`（待办）。
            user_id_type: `user_id` 的标识类型，如 `open_id`/`union_id`/`user_id`。默认为 `open_id`。
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。

        Returns:
            审批任务对象列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [查询用户的任务列表](https://open.feishu.cn/document/server-docs/approval-v4/task/query)

        Examples:
            >>> await client.approval.tasks.query("ou_1", topic="1")  # doctest:+SKIP
            [{'id': 'T1', 'instance_code': 'INST1', ...}]  # noqa: E501
        """
        return await self._client.paginate_get(
            "approval/v4/tasks/query",
            params={"user_id": user_id, "topic": topic, "user_id_type": user_id_type},
            page_size=page_size,
            max_items=max_items,
            items_key="tasks",
        )
