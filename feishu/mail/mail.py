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
    from .events import EventsNamespace
    from .folders import FoldersNamespace
    from .messages import MessagesNamespace
    from .users import UsersNamespace


class MailNamespace(Namespace):
    r"""
    邮箱（Mail）命名空间。

    通过 `client.mail` 访问，作为邮箱地址状态、用户邮箱邮件、文件夹与事件订阅接口的入口。
    子命名空间均在首次访问时惰性创建。

    飞书文档:
        [邮箱 / Mail v1](https://open.feishu.cn/document/mail-v1)
    """

    _events: EventsNamespace | None = None
    _folders: FoldersNamespace | None = None
    _messages: MessagesNamespace | None = None
    _users: UsersNamespace | None = None

    @property
    def events(self) -> EventsNamespace:
        r"""邮箱事件订阅接口命名空间。"""
        if self._events is None:
            from .events import EventsNamespace

            self._events = EventsNamespace(self._client)
        return self._events

    @property
    def folders(self) -> FoldersNamespace:
        r"""邮箱文件夹接口命名空间。"""
        if self._folders is None:
            from .folders import FoldersNamespace

            self._folders = FoldersNamespace(self._client)
        return self._folders

    @property
    def messages(self) -> MessagesNamespace:
        r"""用户邮箱邮件接口命名空间。"""
        if self._messages is None:
            from .messages import MessagesNamespace

            self._messages = MessagesNamespace(self._client)
        return self._messages

    @property
    def users(self) -> UsersNamespace:
        r"""邮箱地址状态查询命名空间。"""
        if self._users is None:
            from .users import UsersNamespace

            self._users = UsersNamespace(self._client)
        return self._users
