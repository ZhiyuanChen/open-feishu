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

from .._envelope import _data
from .._namespace import Namespace
from .._url import quote_segment


def infer_member_type(member_id: str) -> str:
    r"""
    根据协作者 ID 的前缀推断其 `member_type`。

    依据可靠前缀推断权限协作者的 ID 类型，便于在增删协作者时省略 `member_type`：

    | 前缀 | 推断结果 |
    |------|----------|
    | `ou_` 开头 | `openid` |
    | `on_` 开头 | `unionid` |
    | `oc_` 开头 | `openchat` |
    | `od-` / `od_` 开头 | `opendepartmentid` |

    `userid` 没有固定前缀，无法可靠识别，因此当 `member_id` 不匹配上述任一规则时抛出
    `ValueError`，请显式传入 `member_type`。

    Args:
        member_id: 协作者 ID（用户 open/union ID、群 ID 或部门 open ID）。

    Returns:
        推断出的 `member_type` 字符串。

    Raises:
        ValueError: 当无法从 `member_id` 前缀可靠推断类型时抛出。

    Examples:
        >>> infer_member_type("ou_abc")
        'openid'
        >>> infer_member_type("oc_abc")
        'openchat'
    """
    if member_id.startswith("ou_"):
        return "openid"
    if member_id.startswith("on_"):
        return "unionid"
    if member_id.startswith("oc_"):
        return "openchat"
    if member_id.startswith(("od-", "od_")):
        return "opendepartmentid"
    raise ValueError(
        f"cannot infer member_type from {member_id!r}; pass member_type explicitly " "(userid has no reliable prefix)"
    )


class PermissionsNamespace(Namespace):
    r"""
    权限接口命名空间。

    通过 `client.drive.permissions` 访问，封装飞书云文档的权限相关接口。以协作者（member）为主资源，
    提供增加、移除、列举协作者的能力；并将公共权限设置（public）作为一对方法暴露，用于读取与更新文档的
    外部访问、链接分享范围、评论与复制等设置。

    通常无需直接实例化，应通过 `client.drive.permissions` 访问。

    飞书文档:
        [权限概述](https://open.feishu.cn/document/server-docs/docs/permission/permission-member/list)
    """

    async def create(
        self, token: str, member: dict[str, Any], *, type: str, need_notification: bool | None = None
    ) -> NestedDict:
        r"""
        增加协作者权限。

        为指定云文档新增一个协作者。`member` 为协作者条目，需包含 `member_type`（如 `openid`）、
        `member_id` 与 `perm`（如 `view`、`edit`、`full_access`）。`type` 为云文档类型，作为查询参数
        发送，必填。

        Args:
            token: 云文档的 token。
            member: 协作者条目，形如 `{"member_type": "openid", "member_id": "ou_xxx", "perm": "view"}`。
            type: 云文档类型，例如 `doc`、`docx`、`sheet`、`bitable`、`file`、`folder`、`wiki`。
            need_notification: 是否向新增协作者发送通知；为空时省略该查询参数。

        Returns:
            新增结果数据，含 `member` 字段（其中含 `member_type`、`member_id`、`perm` 等）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [增加协作者权限](https://open.feishu.cn/document/server-docs/docs/permission/permission-member/create)
            参见 [feishu.drive.permissions.PermissionsNamespace.delete][]。

        Examples:
            >>> await client.drive.permissions.create(
            ...     "doxcabc",
            ...     {"member_type": "openid", "member_id": "ou_xxx", "perm": "view"},
            ...     type="docx",
            ... )  # doctest:+SKIP
            {'member': {'member_type': 'openid', 'member_id': 'ou_xxx', 'perm': 'view'}}  # noqa: E501
        """
        params: dict[str, Any] = {"type": type}
        if need_notification is not None:
            params["need_notification"] = need_notification
        return await self._request_data(
            "POST", f"drive/v1/permissions/{quote_segment(token)}/members", params=params, json=member
        )

    async def delete(self, token: str, member_id: str, *, type: str, member_type: str | None = None) -> NestedDict:
        r"""
        移除协作者权限。

        移除指定云文档的某个协作者。`type` 为云文档类型，必填。`member_type` 为协作者 ID 类型，
        留空时由 [feishu.drive.permissions.infer_member_type][] 依据 `member_id` 前缀自动推断
        （`userid` 无固定前缀，须显式传入）；二者均作为查询参数发送。

        Args:
            token: 云文档的 token。
            member_id: 待移除协作者的 ID。
            type: 云文档类型，例如 `doc`、`docx`、`sheet`、`bitable`、`file`、`folder`、`wiki`。
            member_type: 协作者 ID 类型，例如 `openid`、`unionid`、`userid`、`openchat`、`opendepartmentid`；
                留空时按 `member_id` 前缀自动推断。

        Returns:
            移除结果数据（通常为空）。

        Raises:
            ValueError: 当 `member_type` 留空且无法从 `member_id` 前缀推断时抛出。
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [移除协作者权限](https://open.feishu.cn/document/server-docs/docs/permission/permission-member/delete)
            参见 [feishu.drive.permissions.PermissionsNamespace.create][]。

        Examples:
            >>> await client.drive.permissions.delete("doxcabc", "ou_xxx", type="docx")  # doctest:+SKIP
            {}
        """
        return await self._request_data(
            "DELETE",
            f"drive/v1/permissions/{quote_segment(token)}/members/{quote_segment(member_id)}",
            params={"type": type, "member_type": member_type or infer_member_type(member_id)},
        )

    async def get_public(self, token: str, *, type: str) -> NestedDict:
        r"""
        获取云文档权限设置。

        获取指定云文档的公共权限设置（如外部访问、可分享、链接分享范围、评论与复制权限等）。
        `type` 为云文档类型，作为查询参数发送，必填。

        Args:
            token: 云文档的 token。
            type: 云文档类型，例如 `doc`、`docx`、`sheet`、`bitable`、`file`、`folder`、`wiki`。

        Returns:
            权限设置数据，含 `permission_public` 字段（其中含 `external_access`、`link_share_entity`、
            `comment_entity`、`copy_entity` 等字段）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取云文档权限设置](https://open.feishu.cn/document/server-docs/docs/permission/permission-public/get)
            参见 [feishu.drive.permissions.PermissionsNamespace.set_public][]。

        Examples:
            >>> await client.drive.permissions.get_public(
            ...     "doxcabc", type="docx"
            ... )  # doctest:+SKIP
            {'permission_public': {'external_access': True, 'link_share_entity': 'tenant_readable', ...}}  # noqa: E501
        """
        return await self._request_data(
            "GET", f"drive/v1/permissions/{quote_segment(token)}/public", params={"type": type}
        )

    async def list(self, token: str, *, type: str, fields: str | None = None) -> list[NestedDict]:
        r"""
        获取协作者列表。

        获取指定云文档的全部协作者。该接口不分页，一次性返回全部协作者。`type` 为云文档类型，
        作为查询参数发送，必填。

        Args:
            token: 云文档的 token。
            type: 云文档类型，例如 `doc`、`docx`、`sheet`、`bitable`、`file`、`folder`、`wiki`。
            fields: 指定返回的协作者字段集合（如 `*`）；为空时省略该查询参数。

        Returns:
            协作者数据列表，每项含 `member_type`、`member_id`、`perm`、`type` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取协作者列表](https://open.feishu.cn/document/server-docs/docs/permission/permission-member/list)
            参见 [feishu.drive.permissions.PermissionsNamespace.create][]。

        Examples:
            >>> await client.drive.permissions.list(
            ...     "doxcabc", type="docx"
            ... )  # doctest:+SKIP
            [{'member_type': 'openid', 'member_id': 'ou_xxx', 'perm': 'view', ...}]  # noqa: E501
        """
        params = {"type": type}
        if fields is not None:
            params["fields"] = fields
        envelope = await self._client.request(
            "GET", f"drive/v1/permissions/{quote_segment(token)}/members", params=params
        )
        return list(_data(envelope).get("items", []))

    async def set_public(self, token: str, settings: dict[str, Any], *, type: str) -> NestedDict:
        r"""
        更新云文档权限设置。

        更新指定云文档的公共权限设置。`settings` 为待更新的设置项（仅传需修改的字段，如
        `link_share_entity`、`comment_entity`、`copy_entity` 等）。`type` 为云文档类型，作为查询
        参数发送，必填。

        Args:
            token: 云文档的 token。
            settings: 待更新的权限设置项，形如 `{"link_share_entity": "tenant_readable"}`。
            type: 云文档类型，例如 `doc`、`docx`、`sheet`、`bitable`、`file`、`folder`、`wiki`。

        Returns:
            更新后的权限设置数据，含 `permission_public` 字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [更新云文档权限设置](https://open.feishu.cn/document/server-docs/docs/permission/permission-public/patch)
            参见 [feishu.drive.permissions.PermissionsNamespace.get_public][]。

        Examples:
            >>> await client.drive.permissions.set_public(
            ...     "doxcabc", {"link_share_entity": "tenant_readable"}, type="docx"
            ... )  # doctest:+SKIP
            {'permission_public': {'link_share_entity': 'tenant_readable', ...}}  # noqa: E501
        """
        return await self._request_data(
            "PATCH", f"drive/v1/permissions/{quote_segment(token)}/public", params={"type": type}, json=settings
        )
