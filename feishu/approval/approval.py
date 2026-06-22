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

from typing import TYPE_CHECKING

from .._namespace import Namespace

if TYPE_CHECKING:
    from .comments import CommentsNamespace
    from .definitions import DefinitionsNamespace
    from .instances import InstancesNamespace
    from .tasks import TasksNamespace


class ApprovalNamespace(Namespace):
    r"""
    审批（Approval）接口命名空间。

    通过 `client.approval` 访问，作为审批定义、实例、任务与评论四个子命名空间的入口：
    [`ApprovalNamespace.definitions`][feishu.approval.approval.ApprovalNamespace.definitions]
    暴露审批定义（approval）的查询，
    [`ApprovalNamespace.instances`][feishu.approval.approval.ApprovalNamespace.instances]
    暴露审批实例（instance）的创建、查询、列举与撤回，
    [`ApprovalNamespace.tasks`][feishu.approval.approval.ApprovalNamespace.tasks]
    暴露审批任务（task）的同意、拒绝与转交，
    [`ApprovalNamespace.comments`][feishu.approval.approval.ApprovalNamespace.comments]
    暴露审批评论（comment）的创建与列举。各子命名空间均在首次访问时惰性创建。
    审批定义以 `approval_code` 标识，依据定义发起的实例以 `instance_id`（或 `instance_code`）
    标识，实例内含若干待办任务与评论。

    通常无需直接实例化，应通过 `client.approval` 访问。

    飞书文档:
        [审批概述](https://open.feishu.cn/document/server-docs/approval-v4/overview)
    """

    _comments: CommentsNamespace | None = None
    _definitions: DefinitionsNamespace | None = None
    _instances: InstancesNamespace | None = None
    _tasks: TasksNamespace | None = None

    @property
    def comments(self) -> CommentsNamespace:
        r"""
        审批评论接口命名空间。

        惰性创建并返回 [feishu.approval.comments.CommentsNamespace][]，用于创建与列举审批评论。

        Returns:
            审批评论接口命名空间实例。

        飞书文档:
            [审批 / 评论](https://open.feishu.cn/document/server-docs/approval-v4/instance-comment/create)

        Examples:
            >>> client.approval.comments  # doctest:+SKIP
            <feishu.approval.comments.CommentsNamespace object at ...>
        """
        if self._comments is None:
            from .comments import CommentsNamespace

            self._comments = CommentsNamespace(self._client)
        return self._comments

    @property
    def definitions(self) -> DefinitionsNamespace:
        r"""
        审批定义接口命名空间。

        惰性创建并返回 [feishu.approval.definitions.DefinitionsNamespace][]，用于查询审批定义。

        Returns:
            审批定义接口命名空间实例。

        飞书文档:
            [审批 / 审批定义](https://open.feishu.cn/document/server-docs/approval-v4/approval/get)

        Examples:
            >>> client.approval.definitions  # doctest:+SKIP
            <feishu.approval.definitions.DefinitionsNamespace object at ...>
        """
        if self._definitions is None:
            from .definitions import DefinitionsNamespace

            self._definitions = DefinitionsNamespace(self._client)
        return self._definitions

    @property
    def instances(self) -> InstancesNamespace:
        r"""
        审批实例接口命名空间。

        惰性创建并返回 [feishu.approval.instances.InstancesNamespace][]，用于创建、查询、列举与撤回审批实例。

        Returns:
            审批实例接口命名空间实例。

        飞书文档:
            [审批 / 审批实例](https://open.feishu.cn/document/server-docs/approval-v4/instance/create)

        Examples:
            >>> client.approval.instances  # doctest:+SKIP
            <feishu.approval.instances.InstancesNamespace object at ...>
        """
        if self._instances is None:
            from .instances import InstancesNamespace

            self._instances = InstancesNamespace(self._client)
        return self._instances

    @property
    def tasks(self) -> TasksNamespace:
        r"""
        审批任务接口命名空间。

        惰性创建并返回 [feishu.approval.tasks.TasksNamespace][]，用于同意、拒绝与转交审批任务。

        Returns:
            审批任务接口命名空间实例。

        飞书文档:
            [审批 / 审批任务](https://open.feishu.cn/document/server-docs/approval-v4/task/approve)

        Examples:
            >>> client.approval.tasks  # doctest:+SKIP
            <feishu.approval.tasks.TasksNamespace object at ...>
        """
        if self._tasks is None:
            from .tasks import TasksNamespace

            self._tasks = TasksNamespace(self._client)
        return self._tasks
