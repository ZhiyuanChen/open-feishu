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


class SheetsNamespace(Namespace):
    r"""
    电子表格（Sheets）接口命名空间。

    封装飞书电子表格相关的服务端接口，包括表格的创建、查询、重命名、列举工作表，
    以及基于 v2 数据操作接口的单元格区域读取、写入与追加等能力。

    元数据类接口（创建、查询、重命名、列举工作表）走 sheets/v3；单元格数据读写接口
    （读取、写入、追加）目前仅在 sheets/v2 提供，相关方法直接走 v2 路径，请求体统一为
    `{"valueRange": {"range": ..., "values": ...}}` 形态，请勿与 v3 风格的字段命名混淆。

    通常无需直接实例化，应通过 `client.sheets` 访问。

    飞书文档:
        [电子表格概述](https://open.feishu.cn/document/server-docs/docs/sheets-v3/overview)
    """

    async def append_rows(self, spreadsheet_token: str, range: str, values: list[list[Any]]) -> NestedDict:
        r"""
        在指定区域之后追加行数据。

        该接口走 sheets/v2 数据操作路径（v3 暂未提供单元格读写）。请求体按
        `{"valueRange": {"range": ..., "values": ...}}` 构造。飞书会在 `range` 所在区域
        之后自动寻找空行并追加数据。

        Args:
            spreadsheet_token: 电子表格 token。
            range: 用于定位追加位置的区域，形如 `<sheetId>!<起始位置>:<结束位置>`。
            values: 二维数组形式的追加数据，外层为行、内层为列。

        Returns:
            追加结果数据，通常包含 `spreadsheetToken`、`tableRange`、`updates`
            （内含 `updatedRange`、`updatedRows`、`updatedCells`、`revision`）等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [追加数据](https://open.feishu.cn/document/ukTMukTMukTM/uMjMzUjLzIzM14yMyMTN)

        Examples:
            >>> await client.sheets.append_rows("shtcn_xxx", "Q7PlXT!A1:B2", [["e", "f"]])  # doctest:+SKIP
            {'spreadsheetToken': 'shtcn_xxx', 'tableRange': 'Q7PlXT!A1:B3', 'updates': {'updatedRows': 1}}
        """
        body = {"valueRange": {"range": range, "values": values}}
        return await self._request_data(
            "POST", f"sheets/v2/spreadsheets/{quote_segment(spreadsheet_token)}/values_append", json=body
        )

    async def create(self, title: str, *, folder_token: str | None = None) -> NestedDict:
        r"""
        创建电子表格。

        仅将显式传入的字段写入请求体，未设置的字段会被省略。

        Args:
            title: 电子表格标题。
            folder_token: 目标文件夹 token；为空时创建在云空间根目录。

        Returns:
            创建后的电子表格数据，通常包含 `spreadsheet` 字段，内含
            `spreadsheet_token`、`url`、`title` 等信息。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [创建电子表格](https://open.feishu.cn/document/server-docs/docs/sheets-v3/spreadsheet/create)

        Examples:
            >>> await client.sheets.create("My Sheet", folder_token="fldcn123")  # doctest:+SKIP
            {'spreadsheet': {'spreadsheet_token': 'shtcn_xxx', 'url': '...', 'title': 'My Sheet'}}
        """
        body: dict[str, Any] = {"title": title}
        if folder_token is not None:
            body["folder_token"] = folder_token
        return await self._request_data("POST", "sheets/v3/spreadsheets", json=body)

    async def delete_dimension(
        self,
        spreadsheet_token: str,
        sheet_id: str,
        *,
        major_dimension: str = "ROWS",
        start_index: int,
        end_index: int,
    ) -> NestedDict:
        r"""
        删除工作表中的若干行或列。

        该接口走 sheets/v2 数据操作路径（v3 暂未提供该能力）。请求体按
        `{"dimension": {"sheetId": ..., "majorDimension": ..., "startIndex": ..., "endIndex": ...}}`
        构造。

        注意：按飞书 dimension_range 约定，`start_index` 与 `end_index` 均为 1 起始
        （1-based），且区间为闭区间（含首含尾）。例如 `start_index=2, end_index=3`
        会删除第 2 行与第 3 行两行。`major_dimension` 取值为 `"ROWS"`（行）或
        `"COLUMNS"`（列）。

        Args:
            spreadsheet_token: 电子表格 token。
            sheet_id: 工作表 ID。
            major_dimension: 删除维度，`"ROWS"` 删除行、`"COLUMNS"` 删除列；默认 `"ROWS"`。
            start_index: 起始位置，1 起始，包含该位置。
            end_index: 结束位置，1 起始，包含该位置。

        Returns:
            删除结果数据，通常包含 `spreadsheetToken`、`delCount`、`majorDimension`
            等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [删除行列](https://open.feishu.cn/document/server-docs/docs/sheets-v3/sheet-rowcol/-delete-rows-or-columns)

        Examples:
            >>> await client.sheets.delete_dimension(  # doctest:+SKIP
            ...     "shtcn_xxx", "Q7PlXT", major_dimension="ROWS", start_index=2, end_index=3
            ... )
            {'spreadsheetToken': 'shtcn_xxx', 'delCount': 2, 'majorDimension': 'ROWS'}
        """
        body = {
            "dimension": {
                "sheetId": sheet_id,
                "majorDimension": major_dimension,
                "startIndex": start_index,
                "endIndex": end_index,
            }
        }
        return await self._request_data(
            "DELETE", f"sheets/v2/spreadsheets/{quote_segment(spreadsheet_token)}/dimension_range", json=body
        )

    async def get(self, spreadsheet_token: str) -> NestedDict:
        r"""
        获取电子表格的元数据信息。

        Args:
            spreadsheet_token: 电子表格 token。

        Returns:
            电子表格数据，通常包含 `spreadsheet` 字段，内含 `title`、`owner_id`、
            `url` 等信息。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取电子表格信息](https://open.feishu.cn/document/server-docs/docs/sheets-v3/spreadsheet/get)

        Examples:
            >>> await client.sheets.get("shtcn_xxx")  # doctest:+SKIP
            {'spreadsheet': {'title': 'My Sheet', 'owner_id': 'ou_xxx', 'url': '...'}}
        """
        return await self._request_data("GET", f"sheets/v3/spreadsheets/{quote_segment(spreadsheet_token)}")

    async def list_sheets(self, spreadsheet_token: str) -> list[NestedDict]:
        r"""
        获取电子表格下的所有工作表。

        该接口一次性返回全部工作表，无分页，直接取响应体中的 `sheets` 列表返回。

        Args:
            spreadsheet_token: 电子表格 token。

        Returns:
            工作表数据列表，每项通常包含 `sheet_id`、`title`、`index`、`grid_properties`
            等字段；当电子表格不含任何工作表时返回空列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取工作表](https://open.feishu.cn/document/server-docs/docs/sheets-v3/spreadsheet-sheet/query)

        Examples:
            >>> await client.sheets.list_sheets("shtcn_xxx")  # doctest:+SKIP
            [{'sheet_id': '0b**12', 'title': 'Sheet1', 'index': 0}]
        """
        envelope = await self._client.request(
            "GET", f"sheets/v3/spreadsheets/{quote_segment(spreadsheet_token)}/sheets/query"
        )
        return _data(envelope).get("sheets", []) or []

    async def read_range(
        self, spreadsheet_token: str, range: str, *, value_render_option: str | None = None
    ) -> NestedDict:
        r"""
        读取单个区域的单元格数据。

        该接口走 sheets/v2 数据操作路径（v3 暂未提供单元格读写）。`range` 形如
        `<sheetId>!<起始位置>:<结束位置>`（例如 `Q7PlXT!A1:B2`），将直接拼接到 URL
        路径中。

        Args:
            spreadsheet_token: 电子表格 token。
            range: 读取区域，形如 `<sheetId>!<起始位置>:<结束位置>`。
            value_render_option: 单元格数据的渲染方式，例如 `ToString`、`Formula`、
                `FormattedValue`、`UnformattedValue`；为空时使用接口默认值。

        Returns:
            读取结果数据，通常包含 `valueRange`（内含 `range`、`values`、`revision`）
            与 `revision` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [读取单个范围](https://open.feishu.cn/document/ukTMukTMukTM/ugTMzUjL4EzM14COxMTN)

        Examples:
            >>> await client.sheets.read_range("shtcn_xxx", "Q7PlXT!A1:B2")  # doctest:+SKIP
            {'valueRange': {'range': 'Q7PlXT!A1:B2', 'values': [['a', 'b'], ['c', 'd']]}, 'revision': 12}
        """
        params: dict[str, Any] = {}
        if value_render_option is not None:
            params["valueRenderOption"] = value_render_option
        # ``range`` is structured as ``<sheetId>!<A1>:<A1>``; preserve the ``!`` and ``:``
        # delimiters (a valid range encodes to itself) while still encoding injection
        # characters such as ``/``.
        return await self._request_data(
            "GET",
            f"sheets/v2/spreadsheets/{quote_segment(spreadsheet_token)}/values/{quote_segment(range, safe='!:')}",
            params=params,
        )

    async def rename(self, spreadsheet_token: str, title: str) -> NestedDict:
        r"""
        重命名电子表格。

        Args:
            spreadsheet_token: 电子表格 token。
            title: 新的电子表格标题。

        Returns:
            更新后的电子表格数据，通常包含 `spreadsheet` 字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [修改电子表格属性](https://open.feishu.cn/document/server-docs/docs/sheets-v3/spreadsheet/patch)

        Examples:
            >>> await client.sheets.rename("shtcn_xxx", "New Title")  # doctest:+SKIP
            {'spreadsheet': {'title': 'New Title'}}
        """
        return await self._request_data(
            "PATCH", f"sheets/v3/spreadsheets/{quote_segment(spreadsheet_token)}", json={"title": title}
        )

    async def write_range(self, spreadsheet_token: str, range: str, values: list[list[Any]]) -> NestedDict:
        r"""
        向单个区域写入单元格数据。

        该接口走 sheets/v2 数据操作路径（v3 暂未提供单元格读写）。请求体按
        `{"valueRange": {"range": ..., "values": ...}}` 构造，请勿与 v3 风格的字段命名混淆。

        Args:
            spreadsheet_token: 电子表格 token。
            range: 写入区域，形如 `<sheetId>!<起始位置>:<结束位置>`。
            values: 二维数组形式的写入数据，外层为行、内层为列。

        Returns:
            写入结果数据，通常包含 `spreadsheetToken`、`updatedRange`、`updatedRows`、
            `updatedColumns`、`updatedCells`、`revision` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [向单个范围写入数据](https://open.feishu.cn/document/ukTMukTMukTM/uAjMzUjLwIzM14CMyMTN)

        Examples:
            >>> await client.sheets.write_range("shtcn_xxx", "Q7PlXT!A1:B2", [["a", "b"], ["c", "d"]])  # doctest:+SKIP
            {'spreadsheetToken': 'shtcn_xxx', 'updatedCells': 4, 'updatedRange': 'Q7PlXT!A1:B2', 'revision': 13}
        """
        body = {"valueRange": {"range": range, "values": values}}
        return await self._request_data(
            "PUT", f"sheets/v2/spreadsheets/{quote_segment(spreadsheet_token)}/values", json=body
        )
