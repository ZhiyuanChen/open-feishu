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


class FoldersNamespace(Namespace):
    r"""
    邮箱文件夹命名空间。

    通过 `client.mail.folders` 访问，封装用户邮箱文件夹查询接口。

    飞书文档:
        [列出邮箱文件夹](https://open.feishu.cn/document/mail-v1/user_mailbox-folder/list)
    """

    async def list(self, user_mailbox_id: str, *, folder_type: int | None = None) -> list[NestedDict]:
        r"""
        列出用户邮箱文件夹。

        Args:
            user_mailbox_id: 用户邮箱地址，或用户态调用时的 `me`。
            folder_type: 文件夹类型，`1` 为系统文件夹，`2` 为用户文件夹；为空时省略。

        Returns:
            文件夹列表，每项包含 `id`、`name`、`folder_type` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [列出邮箱文件夹](https://open.feishu.cn/document/mail-v1/user_mailbox-folder/list)

        Examples:
            >>> await client.mail.folders.list("me", folder_type=1)  # doctest:+SKIP
            [{'id': 'INBOX', 'name': '收件箱', 'folder_type': 1, ...}]
        """
        data = await self._request_data(
            "GET",
            f"mail/v1/user_mailboxes/{quote_segment(user_mailbox_id)}/folders",
            params={"folder_type": folder_type},
        )
        return [NestedDict(item) for item in data.get("items", [])]
