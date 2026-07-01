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
    from .apps import AppsNamespace
    from .fields import FieldsNamespace
    from .records import RecordsNamespace
    from .tables import TablesNamespace


class BitableNamespace(Namespace):
    r"""
    多维表格（Bitable）接口命名空间。

    通过 `client.bitable` 访问，作为应用、数据表、字段与记录四个子命名空间的入口：
    [`BitableNamespace.apps`][feishu.bitable.bitable.BitableNamespace.apps] 暴露多维表格应用（app）能力，
    [`BitableNamespace.tables`][feishu.bitable.bitable.BitableNamespace.tables] 暴露数据表（table）能力，
    [`BitableNamespace.fields`][feishu.bitable.bitable.BitableNamespace.fields] 暴露字段（field）能力，
    [`BitableNamespace.records`][feishu.bitable.bitable.BitableNamespace.records] 暴露记录（record）能力。
    多维表格以应用为容器，应用内含若干数据表，每张数据表由字段定义结构、由记录承载数据，常以
    `app_token`、`table_id`、`record_id`、`field_id` 等标识各级对象。各子命名空间均在首次访问时惰性创建。

    通常无需直接实例化，应通过 `client.bitable` 访问。

    飞书文档:
        [多维表格概述](https://open.feishu.cn/document/server-docs/docs/bitable-v1/bitable-overview)
    """

    _apps: AppsNamespace | None = None
    _fields: FieldsNamespace | None = None
    _records: RecordsNamespace | None = None
    _tables: TablesNamespace | None = None

    @property
    def apps(self) -> AppsNamespace:
        r"""
        多维表格应用接口命名空间。

        惰性创建并返回 [feishu.bitable.apps.AppsNamespace][]，用于获取多维表格应用（app）的元数据。

        Returns:
            多维表格应用接口命名空间实例。

        飞书文档:
            [多维表格概述](https://open.feishu.cn/document/server-docs/docs/bitable-v1/bitable-overview)

        Examples:
            >>> client.bitable.apps  # doctest:+SKIP
            <feishu.bitable.apps.AppsNamespace object at ...>
        """
        if self._apps is None:
            from .apps import AppsNamespace

            self._apps = AppsNamespace(self._client)
        return self._apps

    @property
    def fields(self) -> FieldsNamespace:
        r"""
        多维表格字段接口命名空间。

        惰性创建并返回 [feishu.bitable.fields.FieldsNamespace][]，用于列举数据表的字段（field）。

        Returns:
            多维表格字段接口命名空间实例。

        飞书文档:
            [列出字段](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/list)

        Examples:
            >>> client.bitable.fields  # doctest:+SKIP
            <feishu.bitable.fields.FieldsNamespace object at ...>
        """
        if self._fields is None:
            from .fields import FieldsNamespace

            self._fields = FieldsNamespace(self._client)
        return self._fields

    @property
    def records(self) -> RecordsNamespace:
        r"""
        多维表格记录接口命名空间。

        惰性创建并返回 [feishu.bitable.records.RecordsNamespace][]，用于记录（record）的查询、检索、
        创建、更新与删除（含批量操作）。

        Returns:
            多维表格记录接口命名空间实例。

        飞书文档:
            [记录数据结构](https://open.feishu.cn/document/docs/bitable-v1/app-table-record/bitable-record-data-structure-overview)

        Examples:
            >>> client.bitable.records  # doctest:+SKIP
            <feishu.bitable.records.RecordsNamespace object at ...>
        """
        if self._records is None:
            from .records import RecordsNamespace

            self._records = RecordsNamespace(self._client)
        return self._records

    @property
    def tables(self) -> TablesNamespace:
        r"""
        多维表格数据表接口命名空间。

        惰性创建并返回 [feishu.bitable.tables.TablesNamespace][]，用于数据表（table）的列举、创建与删除。

        Returns:
            多维表格数据表接口命名空间实例。

        飞书文档:
            [列出数据表](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table/list)

        Examples:
            >>> client.bitable.tables  # doctest:+SKIP
            <feishu.bitable.tables.TablesNamespace object at ...>
        """
        if self._tables is None:
            from .tables import TablesNamespace

            self._tables = TablesNamespace(self._client)
        return self._tables
