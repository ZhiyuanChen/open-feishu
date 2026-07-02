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


class EventsNamespace(Namespace):
    r"""
    邮箱事件订阅命名空间。

    通过 `client.mail.events` 访问，封装飞书邮箱事件订阅与取消订阅接口。该接口通常需要
    [feishu.client.FeishuClient.as_user][] 派生的用户身份调用。

    飞书文档:
        [订阅事件](https://open.feishu.cn/document/mail-v1/user_mailbox-event/subscribe)
    """

    async def subscribe(self, user_mailbox_id: str, *, event_type: int = 1) -> NestedDict:
        r"""
        订阅指定用户邮箱的邮件相关事件。

        Args:
            user_mailbox_id: 用户邮箱地址，或用户态调用时的 `me`。
            event_type: 事件类型，飞书当前只支持 `1`（邮件相关事件）。

        Returns:
            飞书返回的 `data` 数据体。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [订阅事件](https://open.feishu.cn/document/mail-v1/user_mailbox-event/subscribe)

        Examples:
            >>> await client.as_user(user_token).mail.events.subscribe("me")  # doctest:+SKIP
            {...}
        """
        return await self._request_data(
            "POST",
            f"mail/v1/user_mailboxes/{quote_segment(user_mailbox_id)}/event/subscribe",
            json={"event_type": event_type},
        )

    async def unsubscribe(self, user_mailbox_id: str, *, event_type: int = 1) -> NestedDict:
        r"""
        取消订阅指定用户邮箱的邮件相关事件。

        Args:
            user_mailbox_id: 用户邮箱地址，或用户态调用时的 `me`。
            event_type: 事件类型，飞书当前只支持 `1`（邮件相关事件）。

        Returns:
            飞书返回的 `data` 数据体。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [取消订阅事件](https://open.feishu.cn/document/mail-v1/user_mailbox-event/unsubscribe)

        Examples:
            >>> await client.as_user(user_token).mail.events.unsubscribe("me")  # doctest:+SKIP
            {...}
        """
        return await self._request_data(
            "POST",
            f"mail/v1/user_mailboxes/{quote_segment(user_mailbox_id)}/event/unsubscribe",
            json={"event_type": event_type},
        )
