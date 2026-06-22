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
