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

r"""
飞书入站消息的无状态读取助手。

提供从飞书消息体（`im.message.receive_v1` 事件中的 `message` 对象，或
[feishu.im.messages.IMNamespace.get][] 返回的消息数据）中提取信息的纯函数：
[feishu.im.inbound.message_text][] 提取可读文本，
[feishu.im.inbound.is_mentioned][] 判断机器人是否被提及。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from chanfig import NestedDict


def is_mentioned(message: dict[str, Any], *, open_id: str | None = None, union_id: str | None = None) -> bool:
    r"""
    判断消息是否提及（@）了指定用户。

    遍历消息的 `mentions` 数组，若其中任一条目的 `id` 匹配给定的 `open_id` 或 `union_id`，
    则返回 `True`。不同事件类型与接口版本下，条目的 `id` 既可能是同时含 `open_id`、`union_id`
    的字典，也可能是单一字符串，因此对两种形态都进行匹配。

    Args:
        message: 飞书消息体字典，通常含 `mentions` 数组。
        open_id: 待匹配的用户 open ID；为空表示不按 open ID 匹配。
        union_id: 待匹配的用户 union ID；为空表示不按 union ID 匹配。

    Returns:
        消息提及了指定用户时返回 `True`，否则返回 `False`。

    飞书文档:
        [接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)

    Examples:
        >>> message = {
        ...     "content": '{"text":"@_user_1 hi"}',
        ...     "mentions": [{"key": "@_user_1", "id": {"open_id": "ou_bot", "union_id": "on_bot"}, "name": "Bot"}],
        ... }
        >>> is_mentioned(message, open_id="ou_bot")
        True
        >>> is_mentioned(message, open_id="ou_other")
        False
        >>> is_mentioned(message, union_id="on_bot")
        True
        >>> is_mentioned({"mentions": []}, open_id="ou_bot")
        False
    """
    return any(
        _mention_matches(mention, open_id=open_id, union_id=union_id) for mention in message.get("mentions") or []
    )


def message_content(message: dict[str, Any]) -> NestedDict:
    r"""
    解析飞书消息体中的 `content` JSON。

    Args:
        message: 飞书消息体字典，含 `content`。

    Returns:
        解析后的 `content`；缺失、格式错误或非对象时返回空 [chanfig.NestedDict][]。

    Examples:
        >>> message_content({"content": '{"text":"hi"}'}).text
        'hi'
        >>> message_content({"content": "not json"}) == {}
        True
    """
    raw = message.get("content")
    if raw is None:
        body = message.get("body")
        if isinstance(body, dict):
            raw = body.get("content")
    if not raw:
        return NestedDict()
    try:
        content = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return NestedDict()
    if isinstance(content, NestedDict):
        return content
    if isinstance(content, dict):
        return NestedDict(content)
    return NestedDict()


def message_resource(message: dict[str, Any]) -> NestedDict | None:
    r"""
    从图片或文件消息中提取可下载资源。

    返回值中的 `key` 可传给 [feishu.im.messages.IMNamespace.get_resource][] 的 `file_key`，
    `resource_type` 可作为同名参数传入。

    Args:
        message: 飞书消息体字典。

    Returns:
        资源描述；没有图片或文件资源时返回 `None`。

    Examples:
        >>> message_resource({"message_type": "image", "content": '{"image_key":"img_1"}'}).key
        'img_1'
        >>> message_resource({"message_type": "file", "content": '{"file_key":"file_1","file_name":"a.pdf"}'}).name
        'a.pdf'
    """
    content = message_content(message)
    message_type = str(message.get("message_type") or message.get("msg_type") or "")
    image_key = _string(content.get("image_key"))
    if image_key:
        return NestedDict(
            kind="image",
            key=image_key,
            resource_type="image",
            message_type=message_type,
            name=_string(content.get("file_name")) or _string(content.get("name")),
            mime_type=_string(content.get("mime_type")) or _string(content.get("file_type")),
            size=content.get("size") or content.get("file_size"),
        )

    file_key = _string(content.get("file_key"))
    if file_key:
        return NestedDict(
            kind="file",
            key=file_key,
            resource_type="file",
            message_type=message_type,
            name=_string(content.get("file_name")) or _string(content.get("name")),
            mime_type=_string(content.get("mime_type")) or _string(content.get("file_type")),
            size=content.get("size") or content.get("file_size"),
        )
    return None


def message_resources(message: dict[str, Any]) -> list[NestedDict]:
    r"""
    提取消息中的**全部**可下载资源（图片 / 文件）。

    单张图片或文件消息返回 1 个资源（同 [feishu.im.inbound.message_resource][]）；富文本 `post` 消息可内嵌
    多张图片，逐个返回（按出现顺序、去重）。每个资源的 `key` / `resource_type` 可直接传给
    [feishu.im.messages.IMNamespace.get_resource][]。无资源时返回空列表。

    Args:
        message: 飞书消息体字典。

    Returns:
        资源描述列表；无图片 / 文件资源时为空列表。

    Examples:
        >>> message_resources({"message_type": "image", "content": '{"image_key":"img_1"}'})[0].key
        'img_1'
        >>> post = '{"content":[[{"tag":"img","image_key":"a"}],[{"tag":"img","image_key":"b"}]]}'
        >>> [r.key for r in message_resources({"message_type": "post", "content": post})]
        ['a', 'b']
        >>> message_resources({"message_type": "text", "content": '{"text":"hi"}'})
        []
    """
    single = message_resource(message)
    if single is not None:
        return [single]
    content = message_content(message)
    message_type = str(message.get("message_type") or message.get("msg_type") or "")
    rich = content.get("content") or content.get("elements")
    resources: list[NestedDict] = []
    seen: set[str] = set()
    if isinstance(rich, list):
        for line in rich:
            if not isinstance(line, list):
                continue
            for element in line:
                if not isinstance(element, dict) or element.get("tag") != "img":
                    continue
                image_key = _string(element.get("image_key"))
                if image_key and image_key not in seen:
                    seen.add(image_key)
                    resources.append(
                        NestedDict(
                            kind="image",
                            key=image_key,
                            resource_type="image",
                            message_type=message_type,
                            name=_string(element.get("file_name")) or _string(element.get("name")),
                            mime_type=None,
                            size=None,
                        )
                    )
    return resources


def message_text(message: dict[str, Any]) -> str:
    r"""
    从飞书消息体中提取可读文本。

    解析消息体内的 `content` JSON：对 `text` 类型读取 `content['text']`；对富文本 `post` 类型
    （`content` 含 `content`/`elements` 二维数组）将各段文本以空行拼接，若含 `title` 则以
    Markdown 二级标题形式前置。随后用消息的 `mentions` 数组将文本中的 `@_user_N` 占位符替换为
    `@<姓名>`。

    Args:
        message: 飞书消息体字典，含 `message_type`/`msg_type`、`content`，可选 `mentions`。

    Returns:
        提取并解析后的文本；无法解析时返回空字符串。

    飞书文档:
        [接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)

    Examples:
        >>> text_message = {
        ...     "message_type": "text",
        ...     "content": '{"text":"@_user_1 你好"}',
        ...     "mentions": [{"key": "@_user_1", "name": "小明"}],
        ... }
        >>> message_text(text_message)
        '@小明 你好'
        >>> post_message = {
        ...     "message_type": "post",
        ...     "content": '{"title":"标题","content":[[{"tag":"text","text":"正文"}]]}',
        ... }
        >>> message_text(post_message)
        '## 标题\n\n正文'
    """
    content = message_content(message)
    text = _content_text(content)
    for mention in message.get("mentions") or []:
        key = mention.get("key")
        if key:
            text = text.replace(key, f"@{mention.get('name', '')}")
    return text


def message_body_text(message: dict[str, Any]) -> str:
    r"""
    安全地从飞书消息体中提取并裁剪可读文本。

    在 [feishu.im.inbound.message_text][] 之上加一层防御：解析失败（缺字段 / 非法 JSON）时返回空串而非抛错，
    并去除首尾空白。

    Args:
        message: 飞书消息体（事件中的 `message` 节点）。

    Returns:
        裁剪后的可读文本；无法解析时返回空串。

    Examples:
        >>> import json
        >>> message_body_text({"message_type": "text", "content": json.dumps({"text": "  hi  "})})
        'hi'
        >>> message_body_text({"message_type": "image", "content": "not json"})
        ''
    """
    try:
        return message_text(message).strip()
    except (TypeError, ValueError):
        return ""


def message_sender_label(
    message: Mapping[str, Any],
    *,
    id_formatter: Callable[[str], str] | None = None,
    default: str = "unknown",
) -> str:
    r"""
    返回消息发送者的可读名称；无姓名时回退到其 ID（可经 `id_formatter` 转换），再不行用 `default`。

    Args:
        message: 飞书消息体。
        id_formatter: 可选的 ID 格式化函数，对回退使用的发送者 ID 应用（如脱敏 / 转中文名）。
        default: 既无姓名也无 ID 时返回的占位值。默认为 `"unknown"`。

    Returns:
        发送者名称、格式化后的 ID，或 `default`。

    Examples:
        >>> message_sender_label({"sender": {"name": "张三"}})
        '张三'
        >>> message_sender_label({"sender": {"open_id": "ou_1"}}, id_formatter=str.upper)
        'OU_1'
        >>> message_sender_label({})
        'unknown'
    """
    sender = message.get("sender") or message.get("sender_id") or {}
    if isinstance(sender, Mapping):
        value = sender.get("name")
        if isinstance(value, str) and value:
            return value
        for key in ("user_id", "open_id", "union_id", "sender_id"):
            value = sender.get(key)
            if isinstance(value, str) and value:
                return id_formatter(value) if id_formatter is not None else value
    return default


def message_transcript(
    messages: Iterable[dict[str, Any]],
    *,
    id_formatter: Callable[[str], str] | None = None,
) -> str:
    r"""
    把一组飞书消息渲染为「发送者: 文本」逐行转录；非文本消息以 `[类型]` 占位。

    Args:
        messages: 飞书消息体的可迭代集合。
        id_formatter: 可选的发送者 ID 格式化函数，见 [feishu.im.inbound.message_sender_label][]。

    Returns:
        逐行转录文本（行间以换行分隔）。

    Examples:
        >>> import json
        >>> msgs = [{"sender": {"name": "张三"}, "message_type": "text", "content": json.dumps({"text": "hi"})}]
        >>> message_transcript(msgs)
        '张三: hi'
    """
    lines = []
    for item in messages:
        sender = message_sender_label(item, id_formatter=id_formatter)
        text = message_body_text(item)
        if not text:
            text = f"[{item.get('msg_type') or item.get('message_type') or 'non-text'}]"
        lines.append(f"{sender}: {text}")
    return "\n".join(lines)


def interactive_card_text(message: dict[str, Any]) -> str:
    r"""
    从交互卡片消息体中提取可读文本（先解出 `content`，再交给 [feishu.im.inbound.card_text][]）。

    Args:
        message: 飞书交互卡片消息体。

    Returns:
        卡片中的可读文本；无可提取内容时返回空串。

    Examples:
        >>> interactive_card_text({"content": '{"elements":[{"tag":"markdown","content":"hi"}]}'})
        'hi'
    """
    return card_text(message_content(message))


def card_text(card: Mapping[str, Any]) -> str:
    r"""
    从飞书卡片中提取可读的 markdown / 文本内容（递归遍历 `body.elements` 与顶层 `elements`）。

    Args:
        card: 飞书卡片字典。

    Returns:
        卡片中各文本片段以空行拼接的结果；无文本时返回空串。

    Examples:
        >>> card_text({"elements": [{"tag": "markdown", "content": "**hi**"}]})
        '**hi**'
    """
    texts: list[str] = []
    body = card.get("body")
    if isinstance(body, Mapping):
        collect_card_text(body.get("elements"), texts)
    collect_card_text(card.get("elements"), texts)
    return "\n\n".join(text.strip() for text in texts if text.strip())


def card_title(card: Mapping[str, Any]) -> str:
    r"""
    提取飞书卡片头部（header）的标题文本。

    Args:
        card: 飞书卡片字典。

    Returns:
        标题文本；无 header / 标题时返回空串。

    Examples:
        >>> card_title({"header": {"title": {"content": "标题"}}})
        '标题'
        >>> card_title({})
        ''
    """
    header = card.get("header")
    if not isinstance(header, Mapping):
        return ""
    title = header.get("title")
    if isinstance(title, Mapping):
        content = title.get("content")
        return content if isinstance(content, str) else ""
    return title if isinstance(title, str) else ""


def collect_card_text(value: Any, texts: list[str]) -> None:
    r"""
    递归收集嵌套卡片元素中承载文本的片段，就地追加到 `texts`。

    遍历 `markdown` 标签的 `content`、`text`（字符串或 `{content}`），以及 `elements` / `columns` / `fields` /
    `body` 等容器键。

    Args:
        value: 卡片元素（列表 / 字典 / 其他）。
        texts: 收集结果的列表，就地追加。

    Examples:
        >>> texts: list[str] = []
        >>> collect_card_text([{"tag": "markdown", "content": "a"}, {"text": "b"}], texts)
        >>> texts
        ['a', 'b']
    """
    if isinstance(value, list):
        for item in value:
            collect_card_text(item, texts)
        return
    if not isinstance(value, Mapping):
        return

    tag = value.get("tag")
    content = value.get("content")
    if tag == "markdown" and isinstance(content, str):
        texts.append(content)
    text = value.get("text")
    if isinstance(text, Mapping):
        text_content = text.get("content")
        if isinstance(text_content, str):
            texts.append(text_content)
    elif isinstance(text, str):
        texts.append(text)

    for key in ("elements", "columns", "fields"):
        collect_card_text(value.get(key), texts)
    collect_card_text(value.get("body"), texts)


def _content_text(content: dict[str, Any]) -> str:
    if "text" in content:
        return content["text"] or ""
    rich = content.get("content") or content.get("elements")
    if not rich:
        return ""
    text = "\n\n".join(
        element["text"] for line in rich for element in line if isinstance(element, dict) and "text" in element
    )
    title = content.get("title")
    if title:
        return f"## {title}\n\n{text}"
    return text


def _mention_matches(mention: dict[str, Any], *, open_id: str | None, union_id: str | None) -> bool:
    ident = mention.get("id")
    if isinstance(ident, dict):
        if open_id is not None and ident.get("open_id") == open_id:
            return True
        if union_id is not None and ident.get("union_id") == union_id:
            return True
        return False
    if open_id is not None and ident == open_id:
        return True
    if union_id is not None and ident == union_id:
        return True
    return False


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
