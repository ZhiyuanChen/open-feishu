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

from .._envelope import _data
from .._namespace import Namespace
from .._url import quote_segment


class WhiteboardsNamespace(Namespace):
    r"""
    画板资源接口命名空间。

    封装飞书开放平台 `board/v1/whiteboards` 资源下的服务端接口，包括获取画板主题、
    列举画板内的全部节点，以及将整块画板导出为图片等能力。画板由若干节点（node）组成，
    节点是画板内容的最小结构单元。

    通常无需直接实例化，应通过客户端的 `client.board.whiteboards` 访问。

    飞书文档:
        [画板概述](https://open.feishu.cn/document/docs/board-v1/overview)
    """

    async def download_as_image(self, whiteboard_id: str) -> bytes:
        r"""
        下载画板为图片。

        将整块画板渲染并导出为图片，返回图片的原始字节内容（通常为 PNG）。

        Args:
            whiteboard_id: 画板的唯一标识。

        Returns:
            画板图片的原始字节内容。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [下载为图片](https://open.feishu.cn/document/docs/board-v1/whiteboard/download_as_image)
            参见 [feishu.board.whiteboards.WhiteboardsNamespace.list_nodes][]。

        Examples:
            >>> import asyncio
            >>> async def main():
            ...     async with FeishuClient("cli_xxx", "secret") as client:
            ...         return await client.board.whiteboards.download_as_image("wb_abc")
            >>> asyncio.run(main())  # doctest: +SKIP
            b'\\x89PNG...'
        """
        return await self._client.download(f"board/v1/whiteboards/{quote_segment(whiteboard_id)}/download_as_image")

    async def get_theme(self, whiteboard_id: str) -> NestedDict:
        r"""
        获取画板主题。

        返回画板当前使用的主题信息。该接口对应开放平台的「获取画板主题」能力，响应数据
        含 `theme` 字段（取值如 `classic`、`minimalist_gray`、`default` 等）。

        Args:
            whiteboard_id: 画板的唯一标识。

        Returns:
            画板主题数据，含 `theme` 字段（画板当前主题的标识）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取画板主题](https://open.feishu.cn/document/ukTMukTMukTM/uUDN04SN0QjL1QDN/board-v1/whiteboard/theme)
            参见 [feishu.board.whiteboards.WhiteboardsNamespace.list_nodes][]。

        Examples:
            >>> await client.board.whiteboards.get_theme("wb_abc")  # doctest:+SKIP
            {'theme': 'classic'}
        """
        return await self._request_data("GET", f"board/v1/whiteboards/{quote_segment(whiteboard_id)}/theme")

    async def list_nodes(self, whiteboard_id: str, *, user_id_type: str | None = None) -> list[NestedDict]:
        r"""
        获取画板内的所有节点。

        一次性返回画板内的全部节点（该接口不分页），节点以 `id`、`type`、`parent_id`、
        `children` 以及对应类型的内容字段（如 `text`、`image`、`composite_shape` 等）描述。

        Args:
            whiteboard_id: 画板的唯一标识。
            user_id_type: 返回数据中的用户 ID 类型，如 `open_id`、`union_id`、`user_id`；
                为空时使用接口默认值。

        Returns:
            画板节点数据列表（取自响应数据的 `nodes` 字段）；画板为空时返回空列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取所有节点](https://open.feishu.cn/document/docs/board-v1/whiteboard-node/list)
            参见 [feishu.board.whiteboards.WhiteboardsNamespace.get_theme][]。

        Examples:
            >>> await client.board.whiteboards.list_nodes("wb_abc")  # doctest:+SKIP
            [{'id': 'n1', 'type': 'composite_shape', ...}, {'id': 'n2', 'type': 'text', ...}]
        """
        params = {"user_id_type": user_id_type}
        envelope = await self._client.request(
            "GET", f"board/v1/whiteboards/{quote_segment(whiteboard_id)}/nodes", params=params
        )
        return _data(envelope).get("nodes", []) or []
