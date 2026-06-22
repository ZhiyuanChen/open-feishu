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

from .._namespace import Namespace
from .._url import quote_segment
from ..consts import MAX_PAGE_SIZE
from ..pagination import paginate


class WikiNamespace(Namespace):
    r"""
    知识库（Wiki）接口命名空间。

    封装飞书知识库相关的服务端接口，包括知识空间的查询，以及知识节点的查询、创建、移动与重命名等能力。
    知识节点是知识库的组织单元，每个节点关联一个文档、表格等实体对象（通过 `obj_token` 与 `obj_type` 标识）。

    通常无需直接实例化，应通过客户端的 `client.wiki` 访问。

    飞书文档:
        [知识库概述](https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-overview)
    """

    async def create_node(
        self,
        space_id: str,
        doc_type: str,
        *,
        parent_node_token: str | None = None,
        title: str | None = None,
        **opts: Any,
    ) -> NestedDict:
        r"""
        在知识空间中创建知识节点。

        仅将显式传入的字段写入请求体，未设置的字段会被省略。额外的关键字参数（`opts`）中值为
        `None` 的项也会被忽略，其余项原样并入请求体。新建节点会同时创建关联的实体对象（如文档）。

        Args:
            space_id: 知识空间 ID。
            doc_type: 节点关联的实体对象类型，例如 `docx`、`doc`、`sheet`、`mindnote`、
                `bitable`、`file` 等。
            parent_node_token: 父节点 token；为空时创建在知识空间根目录下。
            title: 节点标题；为空时使用默认标题。
            **opts: 其他创建参数，例如 `node_type`（默认 `origin`）；值为 `None` 时忽略。

        Returns:
            包含 `node` 字段的数据，`node` 内含新建节点的 `node_token`、`obj_token`、
            `obj_type`、`title` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [创建知识空间节点](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/create)

        Examples:
            >>> await client.wiki.create_node("7001", "docx", title="New Doc")  # doctest:+SKIP
            {'node': {'node_token': 'wikcnnew', 'obj_type': 'docx', 'title': 'New Doc', ...}}  # noqa: E501
        """
        body: dict[str, Any] = {"obj_type": doc_type}
        if parent_node_token is not None:
            body["parent_node_token"] = parent_node_token
        if title is not None:
            body["title"] = title
        body.update({k: v for k, v in opts.items() if v is not None})
        return await self._request_data("POST", f"wiki/v2/spaces/{quote_segment(space_id)}/nodes", json=body)

    async def get_node(self, token: str, *, doc_type: str | None = None) -> NestedDict:
        r"""
        获取知识节点信息。

        通过实体对象的 `token` 查询其对应的知识节点（wiki node）信息，可用于将文档、表格等
        实体反查到所属知识空间与节点。`doc_type` 为空时由飞书按 `token` 自动推断。

        Args:
            token: 实体对象的 token（如文档 `doc`/`docx`、表格 `sheet`、思维笔记 `mindnote`
                等的 token），也可直接传入知识节点的 `node_token`（搭配 `doc_type="wiki"`）。
            doc_type: 实体对象类型，例如 `doc`、`docx`、`sheet`、`mindnote`、`bitable`、`file`、
                `wiki` 等；为空时省略该查询参数。

        Returns:
            包含 `node` 字段的数据，`node` 内含 `node_token`、`space_id`、`obj_token`、
            `obj_type`、`title`、`parent_node_token` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取知识空间节点信息](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/get_node)

        Examples:
            >>> await client.wiki.get_node("doccnxxx")  # doctest:+SKIP
            {'node': {'node_token': 'wikcnxxx', 'space_id': '7001', 'obj_type': 'docx', ...}}  # noqa: E501
        """
        params = {"token": token, "obj_type": doc_type}
        return await self._request_data("GET", "wiki/v2/spaces/get_node", params=params)

    async def get_space(self, space_id: str) -> NestedDict:
        r"""
        获取知识空间信息。

        Args:
            space_id: 知识空间 ID。

        Returns:
            知识空间数据，包含 `space_id`、`name`、`description`、`space_type` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取知识空间信息](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space/get)

        Examples:
            >>> await client.wiki.get_space("7001")  # doctest:+SKIP
            {'space': {'space_id': '7001', 'name': 'Team Wiki', ...}}  # noqa: E501
        """
        return await self._request_data("GET", f"wiki/v2/spaces/{quote_segment(space_id)}")

    async def list_nodes(
        self,
        space_id: str,
        *,
        parent_node_token: str | None = None,
        page_size: int = 50,
        max_items: int | None = None,
    ) -> list[NestedDict]:
        r"""
        获取知识空间下的子节点列表。

        自动翻页并将各页结果拼接为单个列表返回。`page_size` 会被限制在
        [feishu.consts.MAX_PAGE_SIZE][] 以内。`parent_node_token` 为空时返回知识空间根目录下的
        一级节点，否则返回指定父节点下的直接子节点。

        Args:
            space_id: 知识空间 ID。
            parent_node_token: 父节点 token；为空时返回根目录下的一级节点。
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。

        Returns:
            知识节点数据列表，每项包含 `node_token`、`obj_token`、`obj_type`、`title`、
            `has_child` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取知识空间子节点列表](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/list)

        Examples:
            >>> await client.wiki.list_nodes("7001")  # doctest:+SKIP
            [{'node_token': 'wikcn1', 'title': 'Home', ...}, {'node_token': 'wikcn2', ...}]  # noqa: E501
        """
        return await self._client.paginate_get(
            f"wiki/v2/spaces/{quote_segment(space_id)}/nodes",
            params={"parent_node_token": parent_node_token},
            page_size=page_size,
            max_items=max_items,
        )

    async def list_spaces(self, *, page_size: int = 50, max_items: int | None = None) -> list[NestedDict]:
        r"""
        获取知识空间列表。

        自动翻页并将各页结果拼接为单个列表返回。`page_size` 会被限制在
        [feishu.consts.MAX_PAGE_SIZE][] 以内。返回当前身份（租户或用户）有权访问的知识空间。

        Args:
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。

        Returns:
            知识空间数据列表，每项包含 `space_id`、`name`、`description` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取知识空间列表](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space/list)

        Examples:
            >>> await client.wiki.list_spaces()  # doctest:+SKIP
            [{'space_id': '7001', 'name': 'Team Wiki', ...}, {'space_id': '7002', ...}]  # noqa: E501
        """
        return await self._client.paginate_get(
            "wiki/v2/spaces",
            page_size=page_size,
            max_items=max_items,
        )

    async def move_node(self, space_id: str, node_token: str, target_parent_token: str) -> NestedDict:
        r"""
        移动知识节点。

        将 `node_token` 指定的节点移动到同一知识空间内 `target_parent_token` 指定的父节点下。

        Args:
            space_id: 知识空间 ID。
            node_token: 待移动节点的 token。
            target_parent_token: 目标父节点的 token。

        Returns:
            包含 `node` 字段的数据，`node` 内含移动后节点的 `node_token`、
            `parent_node_token` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [移动知识空间节点](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/move)

        Examples:
            >>> await client.wiki.move_node("7001", "wikcn1", "wikcn0")  # doctest:+SKIP
            {'node': {'node_token': 'wikcn1', 'parent_node_token': 'wikcn0', ...}}  # noqa: E501
        """
        body = {"target_parent_token": target_parent_token}
        return await self._request_data(
            "POST", f"wiki/v2/spaces/{quote_segment(space_id)}/nodes/{quote_segment(node_token)}/move", json=body
        )

    async def search(
        self, query: str, *, space_id: str | None = None, page_size: int = 50, max_items: int | None = None
    ) -> list[NestedDict]:
        r"""
        全文检索知识库节点。

        按关键词搜索当前身份可见的知识节点，自动翻页并将各页结果拼接为单个列表返回。`page_size` 会被限制在
        [feishu.consts.MAX_PAGE_SIZE][] 以内。可选 `space_id` 将检索范围限定在指定知识空间内。检索结果给出
        节点的 `obj_token` 与 `obj_type`，可据此通过 [feishu.docx.documents.DocxNamespace][] 等读取正文。

        该接口以调用者身份做权限过滤，通常需以用户身份调用（见 [feishu.client.FeishuClient.as_user][]）。

        Args:
            query: 检索关键词。
            space_id: 限定检索的知识空间 ID；为空时检索全部可见空间。
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。

        Returns:
            匹配的知识节点列表，每项包含 `node_id`、`space_id`、`obj_token`、`obj_type`、`title`、`url` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [搜索 Wiki](https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-qa)

        Examples:
            >>> nodes = await client.as_user("u-xxx").wiki.search("测试规范")  # doctest:+SKIP
            >>> [n["title"] for n in nodes]  # doctest:+SKIP
            ['Testing Standards (wip)', ...]
        """
        body: dict[str, Any] = {"query": query}
        if space_id is not None:
            body["space_id"] = space_id

        async def fetch(page_token: str | None) -> NestedDict:
            params = {"page_size": min(page_size, MAX_PAGE_SIZE), "page_token": page_token}
            return await self._client.request("POST", "wiki/v1/nodes/search", params=params, json=body)

        return await paginate(fetch, max_items=max_items)

    async def update_node_title(self, space_id: str, node_token: str, title: str) -> NestedDict:
        r"""
        更新知识节点的标题。

        Args:
            space_id: 知识空间 ID。
            node_token: 待更新节点的 token。
            title: 新的节点标题。

        Returns:
            接口返回的数据（通常为空）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [更新知识空间节点标题](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/update_title)

        Examples:
            >>> await client.wiki.update_node_title("7001", "wikcn1", "New Title")  # doctest:+SKIP
            {}
        """
        body = {"title": title}
        return await self._request_data(
            "POST",
            f"wiki/v2/spaces/{quote_segment(space_id)}/nodes/{quote_segment(node_token)}/update_title",
            json=body,
        )
