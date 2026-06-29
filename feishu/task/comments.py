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
from typing import Any

from chanfig import NestedDict

from .._namespace import Namespace
from .._url import quote_segment


class CommentsNamespace(Namespace):
    r"""
    任务评论（Comment）接口命名空间。

    通过 `client.task.comments` 访问，封装飞书任务 v2 中评论的增删改查（创建、列举、更新、删除）。
    评论挂载在某个资源上，由 `resource_type`（默认 `task`）与 `resource_id`（任务的 `guid`）共同定位。

    通常无需直接实例化，应通过 `client.task.comments` 访问。

    飞书文档:
        [创建评论](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/task-v2/comment/create)
    """

    async def create(
        self,
        resource_id: str,
        content: str,
        *,
        resource_type: str = "task",
        user_id_type: str | None = None,
    ) -> NestedDict:
        r"""
        在任务上创建评论。

        Args:
            resource_id: 被评论资源的 ID；当 `resource_type` 为 `task` 时即任务的 `guid`。
            content: 评论内容文本。
            resource_type: 被评论资源的类型，默认 `task`。
            user_id_type: 用户 ID 的类型，如 `open_id`、`union_id`、`user_id`；为空时使用接口默认值。

        Returns:
            创建结果数据，含 `comment` 字段，内含 `id`、`content`、`creator`、`resource_id`、`resource_type`、
            `created_at` 等信息。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [创建评论](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/task-v2/comment/create)

        Examples:
            >>> await client.task.comments.create("d116...", "已完成初稿")  # doctest:+SKIP
            {'comment': {'id': '7654...', 'content': '已完成初稿', 'resource_id': 'd116...'}}
        """
        params: dict[str, Any] = {}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        body = {"content": content, "resource_type": resource_type, "resource_id": resource_id}
        return await self._request_data("POST", "task/v2/comments", params=params, json=body)

    async def list(
        self,
        resource_id: str,
        *,
        resource_type: str = "task",
        user_id_type: str | None = None,
        page_size: int = 50,
        max_items: int | None = None,
    ) -> builtins.list[NestedDict]:
        r"""
        列举某个任务下的评论。

        自动翻页并汇总目标资源上的评论。

        Args:
            resource_id: 资源 ID；当 `resource_type` 为 `task` 时即任务的 `guid`。
            resource_type: 资源类型，默认 `task`。
            user_id_type: 用户 ID 的类型，如 `open_id`、`union_id`、`user_id`；为空时使用接口默认值。
            page_size: 每页数量。默认为 50；超过 [feishu.consts.MAX_PAGE_SIZE][] 时由客户端收敛。
            max_items: 最多返回的评论数量，`None` 表示不限制。默认为 `None`。

        Returns:
            评论对象列表（`data.items`）；无评论时返回空列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取评论列表](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/task-v2/comment/list)

        Examples:
            >>> await client.task.comments.list("d116...")  # doctest:+SKIP
            [{'id': '7654...', 'content': '已完成初稿'}]
        """
        params: dict[str, Any] = {"resource_type": resource_type, "resource_id": resource_id}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        return await self._client.paginate_get(
            "task/v2/comments", params=params, page_size=page_size, max_items=max_items
        )

    async def patch(self, comment_id: str, content: str) -> NestedDict:
        r"""
        更新评论内容。

        仅评论的创建者可编辑评论内容。需要 `task:comment:write` 权限。

        Args:
            comment_id: 评论的唯一标识 `id`。
            content: 更新后的评论内容文本。

        Returns:
            更新后的评论数据，含 `comment` 字段，内含 `id`、`content`、`creator`、`resource_id`、
            `resource_type` 等信息。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [更新评论](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/task-v2/comment/patch)

        Examples:
            >>> await client.task.comments.patch("7654...", "已完成终稿")  # doctest:+SKIP
            {'comment': {'id': '7654...', 'content': '已完成终稿'}}
        """
        body = {"comment": {"content": content}, "update_fields": ["content"]}
        return await self._request_data("PATCH", f"task/v2/comments/{quote_segment(comment_id)}", json=body)

    async def delete(self, comment_id: str) -> NestedDict:
        r"""
        删除评论。

        需要 `task:comment:write` 权限。

        Args:
            comment_id: 评论的唯一标识 `id`。

        Returns:
            空数据体（接口成功时不返回额外字段）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [删除评论](https://open.feishu.cn/document/task-v2/comment/delete)

        Examples:
            >>> await client.task.comments.delete("7654...")  # doctest:+SKIP
            {}
        """
        return await self._request_data("DELETE", f"task/v2/comments/{quote_segment(comment_id)}")
