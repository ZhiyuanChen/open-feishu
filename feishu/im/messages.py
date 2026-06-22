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

import json
import uuid as _uuid
from email.utils import parseaddr
from typing import TYPE_CHECKING, Any

from chanfig import NestedDict

from .._envelope import _data
from .._namespace import Namespace
from .._url import quote_segment
from ..errors import FeishuApiError

if TYPE_CHECKING:
    from .chats import ChatNamespace
    from .pins import PinsNamespace
    from .reactions import ReactionsNamespace


def infer_receive_id_type(receive_id: str) -> str:
    r"""
    根据接收者 ID 的形态推断其 ID 类型。

    根据 ID 的前缀或形态推断 `receive_id_type`，便于在调用消息接口时省略该参数：

    | 前缀 / 形态 | 推断结果 |
    |------------|----------|
    | `oc_` 开头 | `chat_id` |
    | `ou_` 开头 | `open_id` |
    | `on_` 开头 | `union_id` |
    | 合法邮箱地址 | `email` |

    仅依据可靠的前缀与邮箱形态进行推断。`user_id` 没有固定前缀或长度特征，无法可靠识别，
    因此当 `receive_id` 不匹配上述任一规则时直接抛出 `ValueError`，请显式传入 `receive_id_type`。

    Args:
        receive_id: 接收者 ID，可以是 chat ID、open ID、union ID 或邮箱。

    Returns:
        推断出的 ID 类型字符串。

    Raises:
        ValueError: 无法从 `receive_id` 推断出 ID 类型时抛出，此时请显式传入 `receive_id_type`。

    飞书文档:
        [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)

    Examples:
        >>> infer_receive_id_type("oc_abc")
        'chat_id'
        >>> infer_receive_id_type("ou_abc")
        'open_id'
        >>> infer_receive_id_type("on_abc")
        'union_id'
        >>> infer_receive_id_type("alice@example.com")
        'email'
        >>> infer_receive_id_type("abcd1234")
        Traceback (most recent call last):
            ...
        ValueError: cannot infer receive_id_type from 'abcd1234'; pass receive_id_type explicitly
        >>> infer_receive_id_type("garbage")
        Traceback (most recent call last):
            ...
        ValueError: cannot infer receive_id_type from 'garbage'; pass receive_id_type explicitly
    """
    if receive_id.startswith("oc_"):
        return "chat_id"
    if receive_id.startswith("ou_"):
        return "open_id"
    if receive_id.startswith("on_"):
        return "union_id"
    if parseaddr(receive_id)[1] and "@" in receive_id:
        return "email"
    raise ValueError(f"cannot infer receive_id_type from {receive_id!r}; pass receive_id_type explicitly")


def infer_msg_type(content: dict[str, Any] | str) -> str:
    r"""
    根据消息内容的形态推断 `msg_type`。

    仅对高置信度的内容形态做推断，无法判定时回退为 `text`；调用方始终可显式传入
    `msg_type` 覆盖推断结果。

    Args:
        content: 消息内容，字典或已序列化的 JSON 字符串。

    Returns:
        推断出的 `msg_type`，取值为 `text`、`image`、`file`、`post` 或 `interactive`。

    Examples:
        >>> infer_msg_type({"image_key": "img_v2_x"})
        'image'
        >>> infer_msg_type({"file_key": "file_v2_x"})
        'file'
        >>> infer_msg_type({"config": {}, "elements": []})
        'interactive'
        >>> infer_msg_type({"text": "hi"})
        'text'
        >>> infer_msg_type("already json")
        'text'
    """
    if not isinstance(content, dict):
        return "text"
    if "text" in content:
        return "text"
    if "image_key" in content:
        return "image"
    if "file_key" in content:
        return "file"
    if "post" in content:
        return "post"
    if content.get("type") == "template" or any(k in content for k in ("config", "elements", "header")):
        return "interactive"
    return "text"


class IMNamespace(Namespace):
    r"""
    即时消息（IM）接口命名空间。

    封装飞书消息相关的服务端接口，包括发送、回复、编辑、撤回、转发、查询消息以及读取已读用户列表等能力。
    通过 [`IMNamespace.chats`][feishu.im.messages.IMNamespace.chats] 可进一步访问群组相关接口。

    通常无需直接实例化，应通过客户端的 `client.im` 访问。

    飞书文档:
        [消息管理概述](https://open.feishu.cn/document/server-docs/im-v1/message/intro)
    """

    _chats: ChatNamespace | None = None
    _pins: PinsNamespace | None = None
    _reactions: ReactionsNamespace | None = None

    @property
    def chats(self) -> ChatNamespace:
        r"""
        群组接口命名空间。

        惰性创建并返回 [feishu.im.chats.ChatNamespace][]，用于创建、查询、更新、解散群组以及管理群成员。

        Returns:
            群组接口命名空间实例。

        飞书文档:
            [群组管理概述](https://open.feishu.cn/document/server-docs/group/chat/intro)

        Examples:
            >>> client.im.chats  # doctest:+SKIP
            <feishu.im.chats.ChatNamespace object at ...>
        """
        if self._chats is None:
            from .chats import ChatNamespace

            self._chats = ChatNamespace(self._client)
        return self._chats

    async def forward(
        self, receive_id: str, message_id: str, *, receive_id_type: str | None = None, uuid: str | None = None
    ) -> NestedDict:
        r"""
        转发消息。

        将一条已有消息转发给指定接收者。接收者在前，与
        [feishu.im.messages.IMNamespace.send][] / [feishu.im.messages.IMNamespace.merge_forward][] 一致。

        Args:
            receive_id: 接收者 ID，可为 chat ID、open ID、union ID、邮箱或 user ID。
            message_id: 要转发的消息 ID。
            receive_id_type: 接收者 ID 类型。为空时根据 `receive_id` 自动推断；显式传入时优先生效。
            uuid: 消息唯一标识，用于消息去重；为空时自动生成。

        Returns:
            转发后生成的消息数据。

        Raises:
            ValueError: 未显式传入 `receive_id_type` 且无法从 `receive_id` 推断时抛出。
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [转发消息](https://open.feishu.cn/document/server-docs/im-v1/message/forward)

        Examples:
            >>> await client.im.forward("oc_target", "om_1")  # doctest:+SKIP
            {'message_id': 'om_3', ...}  # noqa: E501
        """
        receive_id_type = receive_id_type or infer_receive_id_type(receive_id)
        params = {"receive_id_type": receive_id_type, "uuid": uuid or str(_uuid.uuid4())}
        return await self._request_data(
            "POST",
            f"im/v1/messages/{quote_segment(message_id)}/forward",
            params=params,
            json={"receive_id": receive_id},
        )

    async def get(self, message_id: str) -> NestedDict:
        r"""
        获取指定消息的内容。

        Args:
            message_id: 消息 ID。

        Returns:
            消息数据，包含 `message_id`、`body` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取指定消息的内容](https://open.feishu.cn/document/server-docs/im-v1/message/get)

        Examples:
            >>> await client.im.get("om_1")  # doctest:+SKIP
            {'message_id': 'om_1', 'body': {'content': '{"text":"hi"}'}, ...}  # noqa: E501
        """
        return await self._request_data("GET", f"im/v1/messages/{quote_segment(message_id)}")

    async def get_resource(self, message_id: str, file_key: str, *, resource_type: str = "image") -> bytes:
        r"""
        获取消息中的资源文件（图片或附件）。

        Args:
            message_id: 消息 ID（以 om_ 开头）。
            file_key: 资源文件 Key，可从消息体中的 image_key / file_key 字段获取。
            resource_type: 资源类型，"image"（默认）或 "file"。
                发送给飞书接口的 type 查询参数。

        Returns:
            资源文件的原始字节内容。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取消息中的资源文件](https://open.feishu.cn/document/server-docs/im-v1/message/get-2)

        Examples:
            >>> import asyncio
            >>> async def main():
            ...     async with FeishuClient("cli_xxx", "secret") as client:
            ...         return await client.im.get_resource("om_1", "img_k1")
            >>> asyncio.run(main())  # doctest: +SKIP
            b'\\x89PNG...'
        """
        return await self._client.download(
            f"im/v1/messages/{quote_segment(message_id)}/resources/{quote_segment(file_key)}",
            params={"type": resource_type},
        )

    async def list_messages(
        self,
        container_id: str,
        *,
        container_id_type: str = "chat",
        sort_type: str = "ByCreateTimeDesc",
        start_time: str | None = None,
        end_time: str | None = None,
        page_size: int = 50,
        max_items: int | None = None,
    ) -> list[NestedDict]:
        r"""
        获取会话历史消息。

        自动翻页并将各页结果拼接为单个列表返回。`page_size` 会被限制在飞书接口上限（50）以内。

        Args:
            container_id: 容器 ID，例如群 chat ID。
            container_id_type: 容器类型，默认为 `chat`。
            sort_type: 排序方式，默认为 `ByCreateTimeDesc`（按创建时间倒序）。
            start_time: 起始时间，秒级时间戳字符串；为空表示不限制。
            end_time: 结束时间，秒级时间戳字符串；为空表示不限制。
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。

        Returns:
            消息数据列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取会话历史消息](https://open.feishu.cn/document/server-docs/im-v1/message/list)

        Examples:
            >>> await client.im.list_messages("oc_chat")  # doctest:+SKIP
            [{'message_id': 'm1', ...}, {'message_id': 'm2', ...}]  # noqa: E501
        """
        return await self._client.paginate_get(
            "im/v1/messages",
            params={
                "container_id": container_id,
                "container_id_type": container_id_type,
                "sort_type": sort_type,
                "start_time": start_time,
                "end_time": end_time,
            },
            page_size=page_size,
            max_items=max_items,
        )

    async def list_reply_chain(
        self,
        message_id: str,
        *,
        max_items: int | None = None,
        max_chars: int | None = None,
        oldest_first: bool = True,
    ) -> list[NestedDict]:
        r"""
        获取一条消息的回复链（自身及其全部父级消息）。

        从 `message_id` 指向的消息出发，沿 `parent_id` 逐级向上抓取父消息，直至以下任一条件满足：
        不再存在 `parent_id`、已收集的消息数量达到 `max_items`、或累计的消息体长度达到 `max_chars`。
        抓取过程中若某条父消息缺失（飞书接口返回业务错误），视为回复链终点并优雅停止。

        Args:
            message_id: 链尾消息 ID（以 `om_` 开头），从此条消息开始向上回溯。
            max_items: 最多返回的消息条数；为空表示不限制。
            max_chars: 累计消息体长度上限，达到后停止向上回溯；为空表示不限制。
            oldest_first: 为 `True`（默认）时按时间正序返回（最早的消息在前），为 `False` 时逆序返回。

        Returns:
            回复链上的消息数据列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取指定消息的内容](https://open.feishu.cn/document/server-docs/im-v1/message/get)

        Examples:
            >>> await client.im.list_reply_chain("om_leaf")  # doctest:+SKIP
            [{'message_id': 'om_root', ...}, {'message_id': 'om_leaf', ...}]  # noqa: E501
        """
        chain: list[NestedDict] = []
        total_chars = 0
        current_id: str | None = message_id
        while current_id is not None:
            try:
                envelope = await self._client.request("GET", f"im/v1/messages/{quote_segment(current_id)}")
            except FeishuApiError:
                break
            items = _data(envelope).get("items") or []
            if not items:
                break
            message = items[0]
            chain.append(message)
            total_chars += len(_message_body_content(message))
            if max_items is not None and len(chain) >= max_items:
                break
            if max_chars is not None and total_chars >= max_chars:
                break
            current_id = message.get("parent_id")
        if oldest_first:
            chain.reverse()
        return chain

    async def merge_forward(
        self,
        receive_id: str,
        message_id_list: list[str],
        *,
        receive_id_type: str | None = None,
        uuid: str | None = None,
    ) -> NestedDict:
        r"""
        合并转发消息。

        将多条已有消息合并为一条转发给指定接收者。

        Args:
            receive_id: 接收者 ID，可为 chat ID、open ID、union ID、邮箱或 user ID。
            message_id_list: 要合并转发的消息 ID 列表。
            receive_id_type: 接收者 ID 类型。为空时根据 `receive_id` 自动推断；显式传入时优先生效。
            uuid: 消息唯一标识，用于消息去重；为空时不发送该字段。

        Returns:
            合并转发后生成的消息数据。

        Raises:
            ValueError: 未显式传入 `receive_id_type` 且无法从 `receive_id` 推断时抛出。
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [合并转发消息](https://open.feishu.cn/document/server-docs/im-v1/message/merge_forward)

        Examples:
            >>> await client.im.merge_forward("oc_target", ["om_1", "om_2"])  # doctest:+SKIP
            {'message_id': 'om_3', ...}  # noqa: E501
        """
        receive_id_type = receive_id_type or infer_receive_id_type(receive_id)
        body: dict[str, Any] = {"receive_id": receive_id, "message_id_list": message_id_list}
        if uuid is not None:
            body["uuid"] = uuid
        return await self._request_data(
            "POST", "im/v1/messages/merge_forward", params={"receive_id_type": receive_id_type}, json=body
        )

    async def patch(self, message_id: str, card: dict[str, Any] | str) -> NestedDict:
        r"""
        更新应用发送的消息卡片。

        仅更新卡片内容，请求体不含 `msg_type`，适用于交互式卡片的局部刷新。

        Args:
            message_id: 要更新的卡片消息 ID。
            card: 新的卡片内容，可为卡片字典或已序列化的 JSON 字符串。

        Returns:
            更新后的消息数据。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [更新应用发送的消息卡片](https://open.feishu.cn/document/server-docs/im-v1/message-card/patch)

        Examples:
            >>> await client.im.patch("om_1", {"elements": [{"tag": "div"}]})  # doctest:+SKIP
            {'message_id': 'om_1', ...}  # noqa: E501
        """
        body = {"content": _content(card)}
        return await self._request_data("PATCH", f"im/v1/messages/{quote_segment(message_id)}", json=body)

    @property
    def pins(self) -> PinsNamespace:
        r"""
        消息 Pin 接口命名空间。

        惰性创建并返回 [feishu.im.pins.PinsNamespace][]，用于将消息 Pin 到会话、取消 Pin、列举 Pin 消息。

        Returns:
            消息 Pin 接口命名空间实例。

        飞书文档:
            [Pin 概述](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/pin/create)

        Examples:
            >>> client.im.pins  # doctest:+SKIP
            <feishu.im.pins.PinsNamespace object at ...>
        """
        if self._pins is None:
            from .pins import PinsNamespace

            self._pins = PinsNamespace(self._client)
        return self._pins

    async def push_follow_up(self, message_id: str, follow_ups: str | dict[str, Any]) -> NestedDict:
        r"""
        为消息添加跟随气泡。

        Args:
            message_id: 消息 ID（以 om_ 开头）。
            follow_ups: 跟随气泡内容，可为字符串或字典。
                - 传入字符串时，自动封装为 {"follow_ups": [{"content": follow_ups}]}。
                - 传入字典时直接作为请求体发送（调用方需确保包含 "follow_ups" 键）。

        Returns:
            接口返回的数据（通常为空字典）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [添加跟随气泡](https://open.feishu.cn/document/server-docs/im-v1/message/push_follow_up)

        Examples:
            >>> import asyncio
            >>> async def main():
            ...     async with FeishuClient("cli_xxx", "secret") as client:
            ...         return await client.im.push_follow_up("om_1", "点击查看详情")
            >>> asyncio.run(main())  # doctest: +SKIP
            {}
        """
        if isinstance(follow_ups, str):
            follow_ups = {"follow_ups": [{"content": follow_ups}]}
        return await self._request_data(
            "POST", f"im/v1/messages/{quote_segment(message_id)}/push_follow_up", json=follow_ups
        )

    @property
    def reactions(self) -> ReactionsNamespace:
        r"""
        消息表情回复接口命名空间。

        惰性创建并返回 [feishu.im.reactions.ReactionsNamespace][]，用于添加、删除与列举消息表情回复。

        Returns:
            消息表情回复接口命名空间实例。

        飞书文档:
            [表情回复概述](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message-reaction/create)

        Examples:
            >>> client.im.reactions  # doctest:+SKIP
            <feishu.im.reactions.ReactionsNamespace object at ...>
        """
        if self._reactions is None:
            from .reactions import ReactionsNamespace

            self._reactions = ReactionsNamespace(self._client)
        return self._reactions

    async def read_users(
        self, message_id: str, *, user_id_type: str = "open_id", max_items: int | None = None
    ) -> list[NestedDict]:
        r"""
        查询消息已读用户列表。

        自动翻页并将各页结果拼接为单个列表返回。

        Args:
            message_id: 消息 ID。
            user_id_type: 返回的用户 ID 类型，默认为 `open_id`，可选 `union_id`、`user_id`。
            max_items: 最多返回的条数；为空表示返回全部。

        Returns:
            已读用户数据列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [查询消息已读信息](https://open.feishu.cn/document/server-docs/im-v1/message/read_users)

        Examples:
            >>> await client.im.read_users("om_1")  # doctest:+SKIP
            [{'user_id': 'u1', ...}, {'user_id': 'u2', ...}]  # noqa: E501
        """
        return await self._client.paginate_get(
            f"im/v1/messages/{quote_segment(message_id)}/read_users",
            params={"user_id_type": user_id_type},
            page_size=50,
            max_items=max_items,
        )

    async def recall(self, message_id: str) -> NestedDict:
        r"""
        撤回消息。

        Args:
            message_id: 要撤回的消息 ID。

        Returns:
            接口返回的数据（通常为空）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [撤回消息](https://open.feishu.cn/document/server-docs/im-v1/message/delete)

        Examples:
            >>> await client.im.recall("om_1")  # doctest:+SKIP
            {}
        """
        return await self._request_data("DELETE", f"im/v1/messages/{quote_segment(message_id)}")

    async def reply(
        self,
        message_id: str,
        content: dict[str, Any] | str,
        *,
        msg_type: str = "text",
        reply_in_thread: bool | None = None,
        uuid: str | None = None,
    ) -> NestedDict:
        r"""
        回复消息。

        Args:
            message_id: 要回复的消息 ID（以 `om_` 开头）。
            content: 消息内容，可为字典或已序列化的 JSON 字符串。
            msg_type: 消息类型，默认为 `text`。
            reply_in_thread: 是否以话题形式回复。为 `True` 时在话题中回复；为空时沿用被回复消息的形态。
            uuid: 消息唯一标识，用于消息去重；为空时自动生成。

        Returns:
            包含 `message_id` 等字段的消息数据。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply)

        Examples:
            >>> await client.im.reply("om_root", {"text": "hi"})  # doctest:+SKIP
            {'message_id': 'om_2', 'msg_type': 'text', ...}  # noqa: E501
        """
        body: dict[str, Any] = {"msg_type": msg_type, "content": _content(content), "uuid": uuid or str(_uuid.uuid4())}
        if reply_in_thread is not None:
            body["reply_in_thread"] = reply_in_thread
        return await self._request_data("POST", f"im/v1/messages/{quote_segment(message_id)}/reply", json=body)

    async def send(
        self,
        receive_id: str,
        content: dict[str, Any] | str,
        *,
        msg_type: str | None = None,
        receive_id_type: str | None = None,
        uuid: str | None = None,
    ) -> NestedDict:
        r"""
        发送消息。

        Args:
            receive_id: 接收者 ID，可为 chat ID、open ID、union ID、邮箱或 user ID。
            content: 消息内容。可为纯文本字符串（自动按 `text` 类型发送）、内容字典，或已序列化的
                JSON 字符串。
            msg_type: 消息类型。为空时根据 `content` 的形态自动推断（纯文本字符串或含 `text` 推断为
                `text`、含 `image_key` 为 `image`、含 `file_key` 为 `file`、卡片结构为 `interactive`）；
                显式传入时优先生效。常见取值包括 `text`、`post`、`image`、`file`、`interactive`。
            receive_id_type: 接收者 ID 类型。为空时通过
                [feishu.im.messages.infer_receive_id_type][] 根据 `receive_id` 自动推断。
            uuid: 消息唯一标识，用于消息去重；为空时自动生成。

        Returns:
            包含 `message_id` 等字段的消息数据。

        Raises:
            ValueError: 未显式传入 `receive_id_type` 且无法从 `receive_id` 推断时抛出。
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)

        Examples:
            >>> await client.im.send("oc_x", "hi")  # 纯文本字符串按 text 发送  # doctest:+SKIP
            {'message_id': 'om_1', 'msg_type': 'text', ...}  # noqa: E501
            >>> await client.im.send("oc_x", {"image_key": "img_v2_x"})  # msg_type 推断为 image  # doctest:+SKIP
            {'message_id': 'om_1', 'msg_type': 'image', ...}  # noqa: E501
        """
        receive_id_type = receive_id_type or infer_receive_id_type(receive_id)
        content_dict = _to_content_dict(content)
        body = {
            "receive_id": receive_id,
            "msg_type": msg_type or infer_msg_type(content_dict),
            "content": json.dumps(content_dict),
            "uuid": uuid or str(_uuid.uuid4()),
        }
        return await self._request_data(
            "POST", "im/v1/messages", params={"receive_id_type": receive_id_type}, json=body
        )

    async def update(self, message_id: str, content: dict[str, Any] | str, *, msg_type: str = "text") -> NestedDict:
        r"""
        编辑消息。

        用新内容整体替换已发送消息的内容，适用于文本（`text`）与富文本（`post`）消息。
        如需局部更新卡片，请使用 [feishu.im.messages.IMNamespace.patch][]。

        Args:
            message_id: 要编辑的消息 ID。
            content: 新的消息内容，可为字典或已序列化的 JSON 字符串。
            msg_type: 消息类型，默认为 `text`。

        Returns:
            更新后的消息数据。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [编辑消息](https://open.feishu.cn/document/server-docs/im-v1/message/update)

        Examples:
            >>> await client.im.update("om_1", {"text": "edited"})  # doctest:+SKIP
            {'message_id': 'om_1', ...}  # noqa: E501
        """
        body = {"msg_type": msg_type, "content": _content(content)}
        return await self._request_data("PUT", f"im/v1/messages/{quote_segment(message_id)}", json=body)

    async def upload_file(self, file: bytes, file_name: str, *, file_type: str = "stream") -> NestedDict:
        r"""
        上传文件。

        以 `multipart/form-data` 方式上传文件，返回的 `file_key` 可用于
        [feishu.im.messages.IMNamespace.send][] 以 file 类型发送文件消息（msg_type 会自动推断）。

        Args:
            file: 文件的原始字节内容。
            file_name: 文件名称（含扩展名）。
            file_type: 文件类型，默认为 `stream`。可选 `opus`、`mp4`、`pdf`、`doc`、`xls`、`ppt`。

        Returns:
            上传结果数据，含 `file_key` 字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [上传文件](https://open.feishu.cn/document/server-docs/im-v1/file/create)

        Examples:
            >>> await client.im.upload_file(b"hello", "a.txt")  # doctest:+SKIP
            {'file_key': 'file_v2_x'}
        """
        envelope = await self._client.upload(
            "im/v1/files", data={"file_type": file_type, "file_name": file_name}, files={"file": file}
        )
        return _data(envelope)

    async def upload_image(self, image: bytes, *, image_type: str = "message") -> NestedDict:
        r"""
        上传图片。

        以 `multipart/form-data` 方式上传图片，返回的 `image_key` 可用于
        [feishu.im.messages.IMNamespace.send][] 以 image 类型发送图片消息（msg_type 会自动推断）或填充消息卡片。

        Args:
            image: 图片的原始字节内容。
            image_type: 图片用途，默认为 `message`（用于发送消息）。可选 `avatar`（用于设置头像）。

        Returns:
            上传结果数据，含 `image_key` 字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [上传图片](https://open.feishu.cn/document/server-docs/im-v1/image/create)

        Examples:
            >>> await client.im.upload_image(b"\\x89PNG...")  # doctest:+SKIP
            {'image_key': 'img_v2_x'}
        """
        envelope = await self._client.upload("im/v1/images", data={"image_type": image_type}, files={"image": image})
        return _data(envelope)


def _content(content: dict[str, Any] | str) -> str:
    return json.dumps(_to_content_dict(content))


def _message_body_content(message: NestedDict) -> str:
    body = message.get("body") or {}
    return body.get("content") or ""


def _to_content_dict(content: dict[str, Any] | str) -> dict[str, Any]:
    r"""将消息内容规整为字典：字典原样返回；可解析为 JSON 对象的字符串解析之；其余字符串按纯文本封装为 ``{"text": ...}``。"""
    if isinstance(content, dict):
        return content
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        parsed = None
    return parsed if isinstance(parsed, dict) else {"text": content}
