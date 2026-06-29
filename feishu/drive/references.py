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
    A Feishu cloud-document reference extracted from text, URL, or API data.

    `doc_type` is the Feishu object type (`docx`, `wiki`, `sheet`, ...). It may be
    omitted when the caller only has a token; helpers infer the common document and
    wiki prefixes where possible.
    """

    token: str
    doc_type: str | None = None


def parse_document_reference(text: str, *, default_doc_type: str | None = None) -> DocumentReference | None:
    r"""
    Extract a Feishu document reference from a URL or bare token.

    Supports common document/wiki URLs such as `/docx/{token}`, `/docs/{token}`,
    and `/wiki/{token}` plus bare `doxcn...`, `doxc...`, `doccn...`, and
    `wikcn...` tokens. Returns `None` when no reference is found.
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
    Extract a document reference from structured fields, falling back to text.
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
    Extract a meeting-notes document reference from structured fields or text.
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
    Extract a meeting-notes document reference from a Feishu meeting object.
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
    Resolve a document reference to the underlying object token and type.

    Wiki references are resolved through `client.wiki.get_node(..., doc_type="wiki")`.
    Non-wiki references are returned with an inferred document type when possible.
    Calls are made with the `client` supplied by the caller, so user-scoped clients
    created via `client.as_user(user_access_token)` preserve user permission
    boundaries.
    """
    doc_type = _infer_doc_type(reference.token, reference.doc_type)
    if doc_type == "wiki":
        node = (await client.wiki.get_node(reference.token, doc_type="wiki")).get("node") or {}
        return DocumentReference(
            token=str(node.get("obj_token") or reference.token),
            doc_type=str(node.get("obj_type") or ""),
        )
    return DocumentReference(token=reference.token, doc_type=doc_type)


async def raw_document_content(client: Any, reference: DocumentReference, *, lang: int | None = None) -> str:
    r"""
    Read plain-text content for a supported Feishu document reference.

    Currently supports resolved `docx` documents via `client.docx.raw_content`.
    The function intentionally uses the caller-provided client unchanged; pass a
    user-scoped client when reading user-private documents.
    """
    resolved = await resolve_document_reference(client, reference)
    if resolved.doc_type == "docx":
        return await client.docx.raw_content(resolved.token, lang=lang)
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
