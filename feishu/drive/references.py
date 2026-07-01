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

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

DOC_URL_RE = re.compile(r"https?://\S+/(docx|docs|wiki)/([A-Za-z0-9_-]+)")
DOC_TOKEN_RE = re.compile(r"\b(?:doxcn|doxc|doccn|wikcn)[A-Za-z0-9_-]{6,}\b")


@dataclass(frozen=True)
class DocumentReference:
    r"""
    从文本、URL 或接口数据中提取出的飞书云文档引用。

    `doc_type` 是飞书对象类型（如 `docx`、`wiki`、`sheet`）。调用方只有 token 时可以省略；
    本模块会尽量根据常见文档 / 知识库 token 前缀推断类型。
    """

    token: str
    doc_type: str | None = None


def parse_document_reference(text: str, *, default_doc_type: str | None = None) -> DocumentReference | None:
    r"""
    从 URL 或裸 token 中提取飞书文档引用。

    支持 `/docx/{token}`、`/docs/{token}`、`/wiki/{token}` 等常见文档 / 知识库 URL，
    以及裸 `doxcn...`、`doxc...`、`doccn...`、`wikcn...` token。未发现引用时返回 `None`。
    """
    match = DOC_URL_RE.search(text)
    if match:
        url_type, token = match.groups()
        doc_type = "wiki" if url_type == "wiki" else "docx"
        return DocumentReference(token=token, doc_type=doc_type)

    match = DOC_TOKEN_RE.search(text)
    if match:
        token = match.group(0)
        return DocumentReference(token=token, doc_type=_infer_doc_type(token, default_doc_type))

    return None


def document_reference_from_mapping(
    data: Mapping[str, Any],
    *,
    text: str = "",
    token_keys: Sequence[str] = ("document_token", "document_id", "doc_token", "token"),
    url_keys: Sequence[str] = ("document_url", "url"),
    default_doc_type: str | None = None,
    doc_type_key: str = "doc_type",
) -> DocumentReference | None:
    r"""
    从结构化字段中提取文档引用，失败时回退到自由文本解析。

    Args:
        data: 含 token、URL 或类型字段的映射。
        text: 结构化字段未命中时用于兜底解析的自由文本。
        token_keys: 依次尝试读取文档 token 的字段名。
        url_keys: 依次尝试读取文档 URL 的字段名。
        default_doc_type: 未能从 token / URL 推断时使用的默认文档类型。
        doc_type_key: 从 `data` 中读取文档类型的字段名。

    Returns:
        解析出的 [feishu.drive.references.DocumentReference][]，未命中时返回 `None`。
    """
    doc_type = _string_field(data, doc_type_key) or default_doc_type
    for key in token_keys:
        token = _string_field(data, key)
        if token:
            return DocumentReference(token=token, doc_type=_infer_doc_type(token, doc_type))
    for key in url_keys:
        url = _string_field(data, key)
        if url:
            found = parse_document_reference(url, default_doc_type=doc_type)
            if found:
                return found
    return parse_document_reference(text, default_doc_type=doc_type)


def meeting_note_reference_from_mapping(
    data: Mapping[str, Any],
    *,
    text: str = "",
    default_doc_type: str = "docx",
) -> DocumentReference | None:
    r"""
    从结构化字段或文本中提取会议纪要文档引用。

    Args:
        data: 含会议纪要 token、URL 或类型字段的映射。
        text: 结构化字段未命中时用于兜底解析的自由文本。
        default_doc_type: 未能从 token / URL 推断时使用的默认文档类型。

    Returns:
        解析出的 [feishu.drive.references.DocumentReference][]，未命中时返回 `None`。
    """
    doc_type = _string_field(data, "doc_type") or default_doc_type
    return document_reference_from_mapping(
        data,
        text=text,
        token_keys=("meeting_note_token", "note_token", "note_id", "document_token", "document_id", "doc_token"),
        url_keys=("document_url", "note_url", "url"),
        default_doc_type=doc_type,
    )


def meeting_note_reference_from_meeting(
    meeting: Mapping[str, Any],
    *,
    default_doc_type: str = "docx",
) -> DocumentReference | None:
    r"""
    从飞书会议对象中提取会议纪要文档引用。

    Args:
        meeting: 飞书会议对象，含会议纪要 token、URL 或类型字段的映射。
        default_doc_type: 未能从 token / URL 推断时使用的默认文档类型。

    Returns:
        解析出的 [feishu.drive.references.DocumentReference][]，未命中时返回 `None`。
    """
    doc_type = _string_field(meeting, "doc_type") or default_doc_type
    for key in ("meeting_note_token", "note_token", "note_id", "document_token"):
        value = _string_field(meeting, key)
        if value:
            return DocumentReference(token=value, doc_type=_infer_doc_type(value, doc_type))
    for key in ("document_url", "note_url", "url"):
        value = _string_field(meeting, key)
        if value:
            found = parse_document_reference(value, default_doc_type=doc_type)
            if found:
                return found
    return None


async def resolve_document_reference(client: Any, reference: DocumentReference) -> DocumentReference:
    r"""
    将文档引用解析为底层对象 token 与类型。

    知识库引用会通过 [feishu.wiki.spaces.WikiNamespace.get_node][] 解析到实际对象；
    非知识库引用会在可推断时补齐文档类型后原样返回。函数始终使用调用方传入的 `client`，
    因此传入 `client.as_user(user_access_token)` 创建的用户态客户端时，会保留用户权限边界。
    """
    doc_type = _infer_doc_type(reference.token, reference.doc_type)
    if doc_type == "wiki":
        node = (await client.wiki.get_node(reference.token, obj_type="wiki")).get("node") or {}
        return DocumentReference(
            token=str(node.get("obj_token") or reference.token),
            doc_type=str(node.get("obj_type") or ""),
        )
    return DocumentReference(token=reference.token, doc_type=doc_type)


async def raw_document_content(client: Any, reference: DocumentReference, *, lang: int | None = None) -> str:
    r"""
    读取受支持飞书文档引用的纯文本内容。

    当前支持解析后的 `docx` 文档，并通过 [feishu.docx.documents.DocxNamespace.get_raw_content][] 读取。
    函数会原样使用调用方传入的客户端；读取用户私有文档时应传入用户态客户端。

    Args:
        client: 飞书客户端；读取用户私有文档时应传入用户态客户端（如 `client.as_user(...)`）。
        reference: 待读取的文档引用；会先经 [feishu.drive.references.resolve_document_reference][] 解析。
        lang: 内容语言，`0` 默认语言、`1` 中文、`2` 英文；为空时使用接口默认值。

    Returns:
        文档的纯文本内容字符串。

    Raises:
        ValueError: 当解析后的文档类型不受支持时抛出。
    """
    resolved = await resolve_document_reference(client, reference)
    if resolved.doc_type == "docx":
        return await client.docx.get_raw_content(resolved.token, lang=lang)
    raise ValueError(f"raw content is not supported for document type {resolved.doc_type!r}")


def _infer_doc_type(token: str, doc_type: str | None = None) -> str | None:
    if doc_type:
        return "docx" if doc_type == "docs" else doc_type
    if token.startswith("wikcn"):
        return "wiki"
    if token.startswith(("doxcn", "doxc", "doccn")):
        return "docx"
    return None


def _string_field(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None
