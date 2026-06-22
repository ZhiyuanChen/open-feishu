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


class InstancesNamespace(Namespace):
    r"""
    审批实例接口命名空间。

    通过 `client.approval.instances` 访问，封装飞书审批中审批实例（instance）相关的服务端接口，
    包括创建、查询、列举与撤回审批实例等能力。依据审批定义（`approval_code`）发起的实例以
    `instance_id`（或 `instance_code`）标识，实例内含若干待办任务与评论。

    通常无需直接实例化，应通过 `client.approval.instances` 访问。

    飞书文档:
        [审批 / 审批实例](https://open.feishu.cn/document/server-docs/approval-v4/instance/create)
    """

    async def cancel(self, approval_code: str, instance_code: str, user_id: str) -> NestedDict:
        r"""
        撤回审批实例。

        将 `approval_code`、`instance_code`、`user_id` 作为请求体字段发送，撤回指定用户
        发起的审批实例。

        Args:
            approval_code: 审批定义的唯一标识 `approval_code`。
            instance_code: 待撤回审批实例的 `instance_code`。
            user_id: 发起撤回操作的用户 ID。

        Returns:
            接口返回的数据（通常为空）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [撤回审批实例](https://open.feishu.cn/document/server-docs/approval-v4/instance/cancel)

        Examples:
            >>> await client.approval.instances.cancel("ABC123", "INST123", "u1")  # doctest:+SKIP
            {}
        """
        return await self._request_data(
            "POST",
            "approval/v4/instances/cancel",
            json={"approval_code": approval_code, "instance_code": instance_code, "user_id": user_id},
        )

    async def create(self, instance: dict[str, Any]) -> NestedDict:
        r"""
        创建审批实例。

        `instance` 是描述待创建审批实例的请求体，原样作为 JSON 发送，常见键包括
        `approval_code`、`form`、`user_id`、`open_id`、`department_id`、`node_approver_user_id_list`
        等。

        Args:
            instance: 审批实例定义对象，例如
                `{"approval_code": "ABC123", "user_id": "u1", "form": "[...]"}`。

        Returns:
            创建结果数据，含新建实例的 `instance_code` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [创建审批实例](https://open.feishu.cn/document/server-docs/approval-v4/instance/create)

        Examples:
            >>> await client.approval.instances.create({"approval_code": "ABC123", "user_id": "u1"})  # doctest:+SKIP
            {'instance_code': 'INST123'}  # noqa: E501
        """
        return await self._request_data("POST", "approval/v4/instances", json=instance)

    async def get(self, instance_id: str) -> NestedDict:
        r"""
        获取审批实例详情。

        Args:
            instance_id: 审批实例的唯一标识 `instance_id`（即 `instance_code`）。

        Returns:
            审批实例数据，含 `approval_code`、`approval_name`、`status`、`form`、
            `task_list`、`comment_list`、`timeline` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取单个审批实例详情](https://open.feishu.cn/document/server-docs/approval-v4/instance/get)

        Examples:
            >>> await client.approval.instances.get("INST123")  # doctest:+SKIP
            {'approval_code': 'ABC123', 'status': 'PENDING', 'form': '...', ...}  # noqa: E501
        """
        return await self._request_data("GET", f"approval/v4/instances/{quote_segment(instance_id)}")

    async def list(
        self,
        approval_code: str,
        start_time: str,
        end_time: str,
        *,
        page_size: int = 50,
        max_items: int | None = None,
    ) -> list[str]:
        r"""
        批量获取审批实例 ID。

        自动翻页并将各页结果拼接为单个列表返回。`page_size` 会被限制在
        [feishu.consts.MAX_PAGE_SIZE][] 以内。`approval_code`、`start_time`、`end_time`
        为必填查询参数。实例 ID 列表的条目位于响应体的 `instance_code_list` 字段下，且每个
        条目为实例编码（`instance_code`）字符串而非对象。

        Args:
            approval_code: 审批定义的唯一标识 `approval_code`（必填）。
            start_time: 时间范围的起始（毫秒时间戳字符串，必填）。
            end_time: 时间范围的结束（毫秒时间戳字符串，必填）。
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。

        Returns:
            审批实例编码（`instance_code`）字符串列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [批量获取审批实例 ID](https://open.feishu.cn/document/server-docs/approval-v4/instance/list)

        Examples:
            >>> await client.approval.instances.list("ABC123", "1609459200000", "1612137600000")  # doctest:+SKIP
            ['INST1', 'INST2', ...]  # noqa: E501
        """
        return await self._client.paginate_get(
            "approval/v4/instances",
            params={"approval_code": approval_code, "start_time": start_time, "end_time": end_time},
            page_size=page_size,
            max_items=max_items,
            items_key="instance_code_list",
        )
