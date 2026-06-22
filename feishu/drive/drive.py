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
    from .files import FilesNamespace
    from .permissions import PermissionsNamespace


class DriveNamespace(Namespace):
    r"""
    云空间（Drive）接口命名空间。

    通过 `client.drive` 访问，作为文件与权限两个子命名空间的入口：
    [`DriveNamespace.files`][feishu.drive.drive.DriveNamespace.files] 暴露文件、文件夹与文档导出能力，
    [`DriveNamespace.permissions`][feishu.drive.drive.DriveNamespace.permissions] 暴露协作者与公共权限设置能力。
    两个子命名空间均在首次访问时惰性创建。

    通常无需直接实例化，应通过 `client.drive` 访问。

    飞书文档:
        [云空间概述](https://open.feishu.cn/document/server-docs/docs/drive-v1/introduction)
    """

    _files: FilesNamespace | None = None
    _permissions: PermissionsNamespace | None = None

    @property
    def files(self) -> FilesNamespace:
        r"""
        文件接口命名空间。

        惰性创建并返回 [feishu.drive.files.FilesNamespace][]，用于列举、复制、删除文件，批量查询文件元信息，
        新建文件夹，以及上传、下载文件与文档导出等。

        Returns:
            文件接口命名空间实例。

        飞书文档:
            [云空间概述](https://open.feishu.cn/document/server-docs/docs/drive-v1/introduction)

        Examples:
            >>> client.drive.files  # doctest:+SKIP
            <feishu.drive.files.FilesNamespace object at ...>
        """
        if self._files is None:
            from .files import FilesNamespace

            self._files = FilesNamespace(self._client)
        return self._files

    @property
    def permissions(self) -> PermissionsNamespace:
        r"""
        权限接口命名空间。

        惰性创建并返回 [feishu.drive.permissions.PermissionsNamespace][]，用于管理云文档的协作者
        （增删查）与公共权限设置（读取与更新）。

        Returns:
            权限接口命名空间实例。

        飞书文档:
            [权限概述](https://open.feishu.cn/document/server-docs/docs/permission/permission-member/list)

        Examples:
            >>> client.drive.permissions  # doctest:+SKIP
            <feishu.drive.permissions.PermissionsNamespace object at ...>
        """
        if self._permissions is None:
            from .permissions import PermissionsNamespace

            self._permissions = PermissionsNamespace(self._client)
        return self._permissions
