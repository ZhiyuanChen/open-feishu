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
from .._url import quote_segment


class CommentsNamespace(Namespace):
    r"""
    审批评论接口命名空间。

    通过 `client.approval.comments` 访问，封装飞书审批中审批评论（comment）相关的服务端接口，
    包括创建与列举审批评论等能力。评论隶属于某个审批实例（`instance_id`，即 `instance_code`），
    以 `comment_id` 标识。

    通常无需直接实例化，应通过 `client.approval.comments` 访问。

    飞书文档:
        [审批 / 评论](https://open.feishu.cn/document/server-docs/approval-v4/instance-comment/create)
    """

    async def create(self, instance_id: str, comment: dict[str, Any], *, user_id: str | None = None) -> NestedDict:
        r"""
        创建审批评论。

        `comment` 是描述待创建评论的请求体，原样作为 JSON 发送，常见键包括 `content`、
        `at_info_list`、`parent_comment_id` 等。仅当显式传入 `user_id` 时才将其并入查询参数。

        Args:
            instance_id: 审批实例的 `instance_id`（即 `instance_code`）。
            comment: 评论定义对象，例如 `{"content": "请补充材料"}`。
            user_id: 发表评论的用户 ID；为空时省略该参数。

        Returns:
            创建结果数据，含新建评论的 `comment_id` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [创建评论](https://open.feishu.cn/document/server-docs/approval-v4/instance-comment/create)

        Examples:
            >>> await client.approval.comments.create("INST123", {"content": "请补充材料"})  # doctest:+SKIP
            {'comment_id': 'C1'}  # noqa: E501
        """
        params = {}
        if user_id is not None:
            params["user_id"] = user_id
        return await self._request_data(
            "POST", f"approval/v4/instances/{quote_segment(instance_id)}/comments", params=params, json=comment
        )

    async def list(
        self, instance_id: str, *, user_id: str | None = None, page_size: int = 50, max_items: int | None = None
    ) -> list[NestedDict]:
        r"""
        获取审批评论列表。

        自动翻页并将各页结果拼接为单个列表返回。`page_size` 会被限制在
        [feishu.consts.MAX_PAGE_SIZE][] 以内。仅当显式传入 `user_id` 时才将其并入查询参数。
        响应体中评论条目位于 `comments` 字段下。

        Args:
            instance_id: 审批实例的 `instance_id`（即 `instance_code`）。
            user_id: 用户 ID，用于按指定用户视角返回评论；为空时省略该参数。
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。

        Returns:
            评论数据列表，每项包含 `id`、`content`、`create_time`、`commentator`、`replies`
            等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取评论](https://open.feishu.cn/document/server-docs/approval-v4/instance-comment/list)

        Examples:
            >>> await client.approval.comments.list("INST123", user_id="ou_xxx")  # doctest:+SKIP
            [{'id': 'C1', 'content': '请补充材料', ...}, ...]  # noqa: E501
        """
        params: dict[str, Any] = {}
        if user_id is not None:
            params["user_id"] = user_id
        return await self._client.paginate_get(
            f"approval/v4/instances/{quote_segment(instance_id)}/comments",
            params=params,
            page_size=page_size,
            max_items=max_items,
            items_key="comments",
        )
