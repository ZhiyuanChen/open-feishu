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

import builtins
from collections.abc import Sequence
from typing import Any

from chanfig import NestedDict

from .._namespace import Namespace
from .._url import quote_segment
from ..pagination import paginate

MAIL_MESSAGE_PAGE_SIZE_MAX = 20
MAIL_SEARCH_PAGE_SIZE_MAX = 15


class MessagesNamespace(Namespace):
    r"""
    用户邮箱邮件命名空间。

    通过 `client.mail.messages` 访问，封装用户邮箱邮件列表、详情、发送、邮件卡片解析与批量修改接口。
    复杂 MIME 构造由调用方负责；本命名空间仅按飞书 `mail/v1` 的请求体字段做薄封装。

    飞书文档:
        [列出邮件](https://open.feishu.cn/document/mail-v1/user_mailbox-message/list)
    """

    async def batch_modify(
        self,
        user_mailbox_id: str,
        *,
        message_ids: Sequence[str] | None = None,
        add_label_ids: Sequence[str] | None = None,
        remove_label_ids: Sequence[str] | None = None,
        add_folder: str | None = None,
    ) -> NestedDict:
        r"""
        批量修改邮件标签、所属文件夹或已读未读状态。

        Args:
            user_mailbox_id: 用户邮箱地址，或用户态调用时的 `me`。
            message_ids: 需要修改的邮件 ID 列表。
            add_label_ids: 待添加的标签 ID 列表。
            remove_label_ids: 待移除的标签 ID 列表。
            add_folder: 需要移入的文件夹 ID。

        Returns:
            飞书返回的 `data` 数据体。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [批量修改邮件](https://open.feishu.cn/document/mail-v1/user_mailbox-message/batch_modify)

        Examples:
            >>> await client.mail.messages.batch_modify(
            ...     "me", message_ids=["om_xxx"], add_folder="INBOX"
            ... )  # doctest:+SKIP
            {...}
        """
        body = {
            "message_ids": builtins.list(message_ids) if message_ids is not None else None,
            "add_label_ids": builtins.list(add_label_ids) if add_label_ids is not None else None,
            "remove_label_ids": builtins.list(remove_label_ids) if remove_label_ids is not None else None,
            "add_folder": add_folder,
        }
        return await self._request_data(
            "POST",
            f"mail/v1/user_mailboxes/{quote_segment(user_mailbox_id)}/messages/batch_modify",
            json={k: v for k, v in body.items() if v is not None},
        )

    async def get(self, user_mailbox_id: str, message_id: str, *, format: str | None = None) -> NestedDict:
        r"""
        获取邮件详情。

        Args:
            user_mailbox_id: 用户邮箱地址，或用户态调用时的 `me`。
            message_id: 邮件 ID。
            format: 返回内容格式，飞书支持 `full`、`plain_text_full`、`metadata`；为空时省略。

        Returns:
            飞书返回的 `data` 数据体，通常包含 `message` 字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取邮件详情](https://open.feishu.cn/document/mail-v1/user_mailbox-message/get)

        Examples:
            >>> await client.mail.messages.get("me", "om_xxx", format="metadata")  # doctest:+SKIP
            {'message': {...}}
        """
        return await self._request_data(
            "GET",
            f"mail/v1/user_mailboxes/{quote_segment(user_mailbox_id)}/messages/{quote_segment(message_id)}",
            params={"format": format},
        )

    async def get_by_card(
        self,
        user_mailbox_id: str,
        *,
        card_id: str,
        owner_id: str,
        user_id_type: str = "open_id",
    ) -> NestedDict:
        r"""
        获取邮件卡片下的邮件列表。

        Args:
            user_mailbox_id: 用户邮箱地址，或用户态调用时的 `me`。
            card_id: 邮件卡片 ID。
            owner_id: 邮件卡片 Owner ID。
            user_id_type: Owner ID 类型，默认 `open_id`。

        Returns:
            飞书返回的 `data` 数据体，包含 `message_ids` 与 `owner_info` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取邮件卡片下的邮件列表](https://open.feishu.cn/document/mail-v1/user_mailbox-message/get_by_card)

        Examples:
            >>> await client.mail.messages.get_by_card("me", card_id="card_xxx", owner_id="ou_xxx")  # doctest:+SKIP
            {'message_ids': [...], 'owner_info': {...}}
        """
        return await self._request_data(
            "GET",
            f"mail/v1/user_mailboxes/{quote_segment(user_mailbox_id)}/messages/get_by_card",
            params={"card_id": card_id, "owner_id": owner_id, "user_id_type": user_id_type},
        )

    async def list(
        self,
        user_mailbox_id: str,
        *,
        page_size: int = MAIL_MESSAGE_PAGE_SIZE_MAX,
        max_items: int | None = None,
        folder_id: str | None = None,
        only_unread: bool | None = None,
        label_id: str | None = None,
    ) -> builtins.list[str]:
        r"""
        列出用户邮箱中的邮件 ID。

        自动翻页并将各页邮件 ID 拼接为单个列表返回。飞书该接口每页上限为 20，`page_size`
        大于 20 时会在客户端侧收敛。

        Args:
            user_mailbox_id: 用户邮箱地址，或用户态调用时的 `me`。
            page_size: 每页数量，默认 20。
            max_items: 最多返回的邮件 ID 数量；为空表示返回全部。
            folder_id: 文件夹 ID，例如 `INBOX`。
            only_unread: 是否只查询未读邮件。
            label_id: 标签 ID，例如 `FLAGGED`。

        Returns:
            邮件 ID 列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [列出邮件](https://open.feishu.cn/document/mail-v1/user_mailbox-message/list)

        Examples:
            >>> await client.mail.messages.list("me", folder_id="INBOX", max_items=10)  # doctest:+SKIP
            ['om_xxx']
        """
        return await self._client.paginate_get(
            f"mail/v1/user_mailboxes/{quote_segment(user_mailbox_id)}/messages",
            params={"folder_id": folder_id, "only_unread": only_unread, "label_id": label_id},
            page_size=min(page_size, MAIL_MESSAGE_PAGE_SIZE_MAX),
            max_items=max_items,
        )

    async def search(
        self,
        user_mailbox_id: str,
        *,
        query: str | None = None,
        filter: dict[str, Any] | None = None,
        page_size: int = MAIL_SEARCH_PAGE_SIZE_MAX,
        max_items: int | None = None,
    ) -> builtins.list[NestedDict]:
        r"""
        搜索用户当前账户下的邮件。

        自动翻页并将各页搜索结果拼接为单个列表返回。飞书该接口仅支持 `user_access_token`，
        调用方通常应使用 `client.as_user(user_token).mail.messages.search(...)`。

        Args:
            user_mailbox_id: 用户邮箱地址，或用户态调用时的 `me`。
            query: 搜索关键词；为空时省略。
            filter: 搜索过滤条件，原样传给飞书，如 `from`、`to`、`folder`、`is_unread`、`create_time`。
            page_size: 每页数量，默认 15；超过飞书上限时按 15 收敛。
            max_items: 最多返回的搜索结果数量；为空表示返回全部。

        Returns:
            搜索结果列表，每项包含 `id`、`display_info`、`meta_data` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [搜索邮件](https://open.feishu.cn/document/mail-v1/user_mailbox-message/search)

        Examples:
            >>> await client.as_user(user_token).mail.messages.search("me", query="发票")  # doctest:+SKIP
            [{'id': 'om_xxx', 'display_info': {...}, ...}]
        """
        body = {k: v for k, v in {"query": query, "filter": filter}.items() if v is not None}

        async def fetch(page_token: str | None) -> NestedDict:
            return await self._client.request(
                "POST",
                f"mail/v1/user_mailboxes/{quote_segment(user_mailbox_id)}/search",
                params={"page_size": min(page_size, MAIL_SEARCH_PAGE_SIZE_MAX), "page_token": page_token},
                json=body,
            )

        return await paginate(fetch, max_items=max_items)

    async def send(
        self,
        user_mailbox_id: str,
        *,
        subject: str | None = None,
        to: Sequence[dict[str, Any]] | None = None,
        raw: str | None = None,
        cc: Sequence[dict[str, Any]] | None = None,
        bcc: Sequence[dict[str, Any]] | None = None,
        body_html: str | None = None,
        body_plain_text: str | None = None,
        attachments: Sequence[dict[str, Any]] | None = None,
        dedupe_key: str | None = None,
        head_from: dict[str, Any] | None = None,
        **opts: Any,
    ) -> NestedDict:
        r"""
        发送邮件。

        飞书发送接口通常需要用户身份调用；可使用 `client.as_user(user_token).mail.messages.send(...)`。
        `raw` 与附件正文均应由调用方按飞书文档提供 base64url 编码内容。

        Args:
            user_mailbox_id: 用户邮箱地址，或用户态调用时的 `me`。
            subject: 邮件主题。
            to: 收件人列表，每项形如 `{"mail_address": "...", "name": "..."}`。
            raw: base64url 编码后的 EML 数据。
            cc: 抄送人列表。
            bcc: 密送人列表。
            body_html: HTML 正文。
            body_plain_text: 纯文本正文。
            attachments: 附件列表。
            dedupe_key: 去重键。
            head_from: EML 中发件人信息。
            **opts: 其他飞书支持的请求体字段；值为 `None` 时省略。

        Returns:
            飞书返回的 `data` 数据体，通常包含 `message_id` 与 `thread_id`。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [发送邮件](https://open.feishu.cn/document/mail-v1/user_mailbox-message/send)

        Examples:
            >>> await client.as_user(user_token).mail.messages.send(
            ...     "me",
            ...     subject="周报",
            ...     to=[{"mail_address": "ops@example.com"}],
            ...     body_plain_text="本周进展见附件。",
            ... )  # doctest:+SKIP
            {'message_id': 'om_xxx', 'thread_id': 'omt_xxx'}
        """
        body = {
            "subject": subject,
            "to": builtins.list(to) if to is not None else None,
            "raw": raw,
            "cc": builtins.list(cc) if cc is not None else None,
            "bcc": builtins.list(bcc) if bcc is not None else None,
            "body_html": body_html,
            "body_plain_text": body_plain_text,
            "attachments": builtins.list(attachments) if attachments is not None else None,
            "dedupe_key": dedupe_key,
            "head_from": head_from,
        }
        body.update({k: v for k, v in opts.items() if v is not None})
        return await self._request_data(
            "POST",
            f"mail/v1/user_mailboxes/{quote_segment(user_mailbox_id)}/messages/send",
            json={k: v for k, v in body.items() if v is not None},
        )
