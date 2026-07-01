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
from ..pagination import paginate

# Maximum page size accepted by the block-list API; much larger than the generic 50-item limit.
MAX_BLOCK_PAGE_SIZE = 500


class DocxNamespace(Namespace):
    r"""
    新版文档（Docx）接口命名空间。

    封装飞书云文档（Docx）相关的服务端接口，包括创建文档、获取文档元信息与纯文本内容，
    以及获取、追加、更新文档块（block）等能力。文档由若干块组成，块是文档内容的最小结构单元。

    通常无需直接实例化，应通过客户端的 `client.docx` 访问。

    飞书文档:
        [文档概述](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/docx-overview)
    """

    async def append_blocks(
        self,
        document_id: str,
        children: list[dict[str, Any]],
        *,
        block_id: str | None = None,
        index: int | None = None,
    ) -> NestedDict:
        r"""
        在指定块下创建子块。

        将 `children` 作为子块追加到目标块下；`block_id` 为空时以文档根块（即 `document_id`）
        为父块。`index` 控制插入位置（自 0 起），为空时追加到末尾。仅将显式传入的字段写入请求体。

        Args:
            document_id: 文档的唯一标识。
            children: 待创建的子块对象列表，每个元素描述一个块（含 `block_type` 等）。
            block_id: 父块的唯一标识；为空时使用文档根块 `document_id`。
            index: 插入位置（自 0 起）；为空时追加到子块末尾。

        Returns:
            创建结果数据，含新建的 `children` 列表与文档 `document_revision_id` 等。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [创建块](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/create)
            参见 [feishu.docx.documents.DocxNamespace.patch_block][]。

        Examples:
            >>> blocks = [{"block_type": 2, "text": {"elements": [{"text_run": {"content": "hi"}}]}}]
            >>> await client.docx.append_blocks("doxcabc", blocks)  # doctest:+SKIP
            {'children': [{'block_id': 'blk_new', 'block_type': 2, ...}], 'document_revision_id': 13}
        """
        parent = block_id if block_id is not None else document_id
        body: dict[str, Any] = {"children": children}
        if index is not None:
            body["index"] = index
        return await self._request_data(
            "POST",
            f"docx/v1/documents/{quote_segment(document_id)}/blocks/{quote_segment(parent)}/children",
            json=body,
        )

    async def batch_update_blocks(self, document_id: str, requests: list[dict[str, Any]]) -> NestedDict:
        r"""
        批量更新文档块。

        在一次请求中对多个块执行更新操作，`requests` 为更新操作列表，每个元素与
        [feishu.docx.documents.DocxNamespace.patch_block][] 的 `update` 同构，并额外携带
        其作用的 `block_id`。

        Args:
            document_id: 文档的唯一标识。
            requests: 更新操作列表，每个元素形如
                `{"block_id": "blk2", "update_text_elements": {...}}`。

        Returns:
            批量更新结果数据，含更新后的 `blocks` 列表与文档 `document_revision_id` 等。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [批量更新块的内容](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/batch_update)
            参见 [feishu.docx.documents.DocxNamespace.patch_block][]。

        Examples:
            >>> reqs = [{"block_id": "blk2", "update_text_elements": {"elements": []}}]
            >>> await client.docx.batch_update_blocks("doxcabc", reqs)  # doctest:+SKIP
            {'blocks': [{'block_id': 'blk2', ...}], 'document_revision_id': 15}
        """
        return await self._request_data(
            "PATCH",
            f"docx/v1/documents/{quote_segment(document_id)}/blocks/batch_update",
            json={"requests": requests},
        )

    async def create(self, title: str, *, folder_token: str | None = None) -> NestedDict:
        r"""
        创建文档。

        在指定文件夹（或用户云空间根目录）下创建一篇新的空白文档。仅将显式传入的字段
        写入请求体，未设置的字段会被省略。

        Args:
            title: 文档标题。
            folder_token: 目标文件夹 token；为空时创建在云空间根目录下。

        Returns:
            创建后的文档数据，含 `document` 字段（其中含 `document_id`、`revision_id`、`title`）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [创建文档](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/create)
            参见 [feishu.docx.documents.DocxNamespace.get][]。

        Examples:
            >>> await client.docx.create(title="My Doc")  # doctest:+SKIP
            {'document': {'document_id': 'doxcabc', 'revision_id': 1, 'title': 'My Doc'}}
        """
        body = {"title": title}
        if folder_token is not None:
            body["folder_token"] = folder_token
        return await self._request_data("POST", "docx/v1/documents", json=body)

    async def get(self, document_id: str) -> NestedDict:
        r"""
        获取文档基本信息。

        Args:
            document_id: 文档的唯一标识。

        Returns:
            文档元信息数据，含 `document` 字段（其中含 `document_id`、`revision_id`、`title`）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取文档基本信息](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/get)
            参见 [feishu.docx.documents.DocxNamespace.raw_content][]。

        Examples:
            >>> await client.docx.get("doxcabc")  # doctest:+SKIP
            {'document': {'document_id': 'doxcabc', 'revision_id': 12, 'title': 'My Doc'}}
        """
        return await self._request_data("GET", f"docx/v1/documents/{quote_segment(document_id)}")

    async def get_block(self, document_id: str, block_id: str) -> NestedDict:
        r"""
        获取指定块的内容。

        Args:
            document_id: 文档的唯一标识。
            block_id: 块的唯一标识。

        Returns:
            块数据，含 `block_id`、`block_type`、`parent_id` 及对应类型的内容字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取块的内容](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/get)
            参见 [feishu.docx.documents.DocxNamespace.list_blocks][]。

        Examples:
            >>> await client.docx.get_block("doxcabc", "blk2")  # doctest:+SKIP
            {'block_id': 'blk2', 'block_type': 2, 'parent_id': 'doxcabc', ...}
        """
        return await self._request_data(
            "GET", f"docx/v1/documents/{quote_segment(document_id)}/blocks/{quote_segment(block_id)}"
        )

    async def list_blocks(
        self, document_id: str, *, page_size: int = 500, max_items: int | None = None
    ) -> list[NestedDict]:
        r"""
        获取文档所有块。

        自动翻页并将各页结果拼接为单个列表返回，返回的块按文档顺序排列（含各级嵌套块）。
        `page_size` 会被限制在 [feishu.docx.documents.MAX_BLOCK_PAGE_SIZE][] 以内。

        Args:
            document_id: 文档的唯一标识。
            page_size: 每页条数，默认为 500，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。

        Returns:
            文档块数据列表，每个元素含 `block_id`、`block_type`、`parent_id` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取文档所有块](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/list)
            参见 [feishu.docx.documents.DocxNamespace.get_block][]。

        Examples:
            >>> await client.docx.list_blocks("doxcabc")  # doctest:+SKIP
            [{'block_id': 'doxcabc', 'block_type': 1, ...}, {'block_id': 'blk2', 'block_type': 2, ...}]
        """

        # Do not use client.paginate_get here: the block-list endpoint allows MAX_BLOCK_PAGE_SIZE=500, while
        # paginate_get clamps page_size to the generic MAX_PAGE_SIZE=50, which is too small for document blocks.
        async def fetch(page_token: str | None) -> NestedDict:
            params = {
                "page_size": min(page_size, MAX_BLOCK_PAGE_SIZE),
                "page_token": page_token,
            }
            return await self._client.request(
                "GET", f"docx/v1/documents/{quote_segment(document_id)}/blocks", params=params
            )

        return await paginate(fetch, max_items=max_items)

    async def patch_block(self, document_id: str, block_id: str, update: dict[str, Any]) -> NestedDict:
        r"""
        更新指定块的内容。

        `update` 是描述更新操作的请求体，原样作为 JSON 发送，常见键包括
        `update_text_elements`、`update_text_style`、`update_table_property` 等。

        Args:
            document_id: 文档的唯一标识。
            block_id: 待更新块的唯一标识。
            update: 更新操作请求体，例如 `{"update_text_elements": {"elements": [...]}}`。

        Returns:
            更新后的块数据及文档 `document_revision_id` 等。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [更新块的内容](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/patch)
            参见 [feishu.docx.documents.DocxNamespace.batch_update_blocks][]。

        Examples:
            >>> update = {"update_text_elements": {"elements": [{"text_run": {"content": "new"}}]}}
            >>> await client.docx.patch_block("doxcabc", "blk2", update)  # doctest:+SKIP
            {'block': {'block_id': 'blk2', 'block_type': 2, ...}, 'document_revision_id': 14}
        """
        return await self._request_data(
            "PATCH", f"docx/v1/documents/{quote_segment(document_id)}/blocks/{quote_segment(block_id)}", json=update
        )

    async def raw_content(self, document_id: str, *, lang: int | None = None) -> str:
        r"""
        获取文档纯文本内容。

        返回文档去除格式后的纯文本，便于做检索、摘要等文本处理。`lang` 用于控制
        文档中 @ 提及（如人员、文档）等内容的展示语言。

        Args:
            document_id: 文档的唯一标识。
            lang: 内容语言，`0` 默认语言、`1` 中文、`2` 英文；为空时使用接口默认值。

        Returns:
            文档的纯文本内容字符串（取自响应数据的 `content` 字段）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取文档纯文本内容](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/raw_content)
            参见 [feishu.docx.documents.DocxNamespace.list_blocks][]。

        Examples:
            >>> await client.docx.raw_content("doxcabc")  # doctest:+SKIP
            'Hello world\n'
        """
        params = {"lang": lang}
        envelope = await self._client.request(
            "GET", f"docx/v1/documents/{quote_segment(document_id)}/raw_content", params=params
        )
        return _data(envelope)["content"]
