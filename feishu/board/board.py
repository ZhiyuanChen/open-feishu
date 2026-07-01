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
    from .whiteboards import WhiteboardsNamespace


class BoardNamespace(Namespace):
    r"""
    画板（Board）接口命名空间。

    通过 `client.board` 访问，作为画板资源的入口：
    [`BoardNamespace.whiteboards`][feishu.board.board.BoardNamespace.whiteboards]
    暴露飞书开放平台 `board/v1/whiteboards` 资源下的能力。子命名空间在首次访问时惰性创建。

    通常无需直接实例化，应通过 `client.board` 访问。

    飞书文档:
        [画板概述](https://open.feishu.cn/document/docs/board-v1/overview)
    """

    _whiteboards: WhiteboardsNamespace | None = None

    @property
    def whiteboards(self) -> WhiteboardsNamespace:
        r"""
        画板资源接口命名空间。

        惰性创建并返回 [feishu.board.whiteboards.WhiteboardsNamespace][]，用于获取画板主题、
        列举画板节点，以及将画板下载为图片。

        Returns:
            画板资源接口命名空间实例。

        飞书文档:
            [画板概述](https://open.feishu.cn/document/docs/board-v1/overview)

        Examples:
            >>> client.board.whiteboards  # doctest:+SKIP
            <feishu.board.whiteboards.WhiteboardsNamespace object at ...>
        """
        if self._whiteboards is None:
            from .whiteboards import WhiteboardsNamespace

            self._whiteboards = WhiteboardsNamespace(self._client)
        return self._whiteboards
