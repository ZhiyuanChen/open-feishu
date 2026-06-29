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

from chanfig import NestedDict

from .._namespace import Namespace
from .._url import quote_segment
from ..consts import MAX_PAGE_SIZE


class DefinitionsNamespace(Namespace):
    r"""
    审批定义接口命名空间。

    通过 `client.approval.definitions` 访问，封装飞书审批中审批定义（approval）相关的服务端接口。
    审批定义以 `approval_code` 标识，描述一类审批的表单结构、节点流程与状态等元信息。

    通常无需直接实例化，应通过 `client.approval.definitions` 访问。

    飞书文档:
        [审批 / 审批定义](https://open.feishu.cn/document/server-docs/approval-v4/approval/get)
    """

    async def list(
        self,
        *,
        page_size: int = 50,
        max_items: int | None = None,
        locale: str | None = None,
    ) -> list[NestedDict]:
        r"""
        查询当前身份可发起的审批定义列表。

        飞书按调用身份过滤可发起的审批定义；该接口通常需要通过
        [feishu.client.FeishuClient.as_user][] 派生的用户视图调用。

        Args:
            page_size: 每页条数，超过 [feishu.consts.MAX_PAGE_SIZE][] 时由客户端收敛。
            max_items: 最多返回的条数；为空表示返回全部。
            locale: 国际化语言，如 `zh-CN`、`en-US`；为空时省略该参数。

        Returns:
            审批定义摘要列表，每项通常包含 `approval_code` 与 `approval_name`。

        飞书文档:
            [查询审批定义列表](https://feishu.apifox.cn/api-59181909)
        """
        params = {}
        if locale is not None:
            params["locale"] = locale
        items = await self._client.paginate_get(
            "approval/v4/approvals",
            params=params,
            page_size=min(page_size, MAX_PAGE_SIZE),
            max_items=max_items,
        )
        return [NestedDict(item) for item in items if isinstance(item, dict)]

    async def get(self, approval_code: str, *, locale: str | None = None, user_id: str | None = None) -> NestedDict:
        r"""
        查询审批定义。

        获取指定审批定义的详情。仅将显式传入的可选查询参数并入请求，未设置的项会被省略。

        Args:
            approval_code: 审批定义的唯一标识 `approval_code`。
            locale: 国际化语言，如 `zh-CN`、`en-US`；为空时省略该参数。
            user_id: 用户 ID，用于按指定用户视角返回审批定义；为空时省略该参数。

        Returns:
            审批定义数据，含 `approval_name`、`form`、`node_list`、`status` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [查看指定审批定义](https://open.feishu.cn/document/server-docs/approval-v4/approval/get)

        Examples:
            >>> await client.approval.definitions.get("ABC123")  # doctest:+SKIP
            {'approval_name': '请假', 'form': '...', 'node_list': [...], ...}  # noqa: E501
        """
        params = {}
        if locale is not None:
            params["locale"] = locale
        if user_id is not None:
            params["user_id"] = user_id
        return await self._request_data("GET", f"approval/v4/approvals/{quote_segment(approval_code)}", params=params)
