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
    from .tasks import TasksNamespace


class TaskNamespace(Namespace):
    r"""
    任务（Task）接口命名空间。

    通过 `client.task` 访问，作为任务与评论两个子命名空间的入口：
    [`TaskNamespace.tasks`][feishu.task.task.TaskNamespace.tasks] 暴露任务（task）的增删改查，
    [`TaskNamespace.comments`][feishu.task.task.TaskNamespace.comments] 暴露任务评论（comment）能力。
    两个子命名空间均在首次访问时惰性创建。

    通常无需直接实例化，应通过 `client.task` 访问。

    飞书文档:
        [任务概述](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/task-v2/overview)
    """

    _comments: CommentsNamespace | None = None
    _tasks: TasksNamespace | None = None

    @property
    def comments(self) -> CommentsNamespace:
        r"""
        任务评论接口命名空间。

        惰性创建并返回 [feishu.task.comments.CommentsNamespace][]，用于在任务上创建评论与列举评论。

        Returns:
            任务评论接口命名空间实例。

        飞书文档:
            [创建评论](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/task-v2/comment/create)

        Examples:
            >>> client.task.comments  # doctest:+SKIP
            <feishu.task.comments.CommentsNamespace object at ...>
        """
        if self._comments is None:
            from .comments import CommentsNamespace

            self._comments = CommentsNamespace(self._client)
        return self._comments

    @property
    def tasks(self) -> TasksNamespace:
        r"""
        任务接口命名空间。

        惰性创建并返回 [feishu.task.tasks.TasksNamespace][]，用于任务的创建、查询、列举、更新与删除。

        Returns:
            任务接口命名空间实例。

        飞书文档:
            [创建任务](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/task-v2/task/create)

        Examples:
            >>> client.task.tasks  # doctest:+SKIP
            <feishu.task.tasks.TasksNamespace object at ...>
        """
        if self._tasks is None:
            from .tasks import TasksNamespace

            self._tasks = TasksNamespace(self._client)
        return self._tasks
