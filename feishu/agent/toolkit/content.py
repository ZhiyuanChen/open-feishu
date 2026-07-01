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
内容工具工厂：本模块有意汇聚两组「内容写入 / 读取」工具工厂——docx 文档工具与电子表格工具，二者共享同一套
需审批写入语义，故并置于此。

- docx 文档工具：`create_document`（需审批）、`append_to_document`（需审批）、`list_document_blocks`、
  `update_document`（需审批）、`delete_document`（需审批）；辅助 `_block_text`。
- 电子表格工具：`append_to_sheet`（需审批）、`update_sheet_range`（需审批）、`delete_sheet_rows`（需审批）、
  `read_sheet_range`。

（注意：读取「已有」文档正文的工具在 [feishu.agent.toolkit.documents][]，与此处的写入 / 结构工具区分。）
详见 [feishu.agent.toolkit][]。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..result import ToolOutcome, ToolResult
from ..tools import Tool
from ._base import needs_user_auth, resolve_client


def create_document(
    *,
    description: str,
    name: str = "create_document",
    locale: str = "zh-CN",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：在云空间中创建一篇空白文档，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数调用 `client.docx.create(title, folder_token=...)`；`folder_token` 为空时创建在用户云空间根目录下。
    `requires_approval=True` 时由 [feishu.agent.loop.Agent][] 先发审批卡片，用户批准后处理函数才执行写入。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"create_document"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        requires_approval: 是否需用户审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = create_document(description="创建文档")
        >>> tool.name, tool.requires_approval
        ('create_document', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "document title"},
            "folder_token": {
                "type": "string",
                "description": "target folder token; defaults to the user's drive root",
            },
        },
        "required": ["title"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.docx.create(
            arguments["title"],
            folder_token=arguments.get("folder_token"),
        )
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def append_to_document(
    *,
    description: str,
    name: str = "append_to_document",
    locale: str = "zh-CN",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：向 docx 文档末尾追加一个文本段落，返回一个需审批的 [feishu.agent.tools.Tool][]。

    仅**追加**（与 [feishu.agent.toolkit.content.append_to_sheet][] 同名同义）：处理函数将 `text` 包装为单个
    文本段落块（`block_type` 为 `2`），调用 `client.docx.append_blocks(document_id, children)` 追加到文档根块
    末尾，**不能修改 / 删除已有内容**。`requires_approval=True` 时由 [feishu.agent.loop.Agent][] 先发审批卡片，
    用户批准后处理函数才执行写入。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"append_to_document"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        requires_approval: 是否需用户审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = append_to_document(description="向文档追加段落")
        >>> tool.name, tool.requires_approval
        ('append_to_document', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "document id"},
            "text": {"type": "string", "description": "text paragraph to append"},
        },
        "required": ["document_id", "text"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        children = [
            {"block_type": 2, "text": {"elements": [{"text_run": {"content": arguments["text"]}}], "style": {}}}
        ]
        result = await client.docx.append_blocks(arguments["document_id"], children)
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def _block_text(block: Mapping[str, Any]) -> str:
    r"""从 docx 块中尽力提取可读文本：拼接其 `elements` 里所有 `text_run.content`（适用于文本 / 标题等块）。"""
    parts: list[str] = []
    for value in block.values():
        elements = value.get("elements") if isinstance(value, Mapping) else None
        if isinstance(elements, list):
            for element in elements:
                run = element.get("text_run") if isinstance(element, Mapping) else None
                if isinstance(run, Mapping) and isinstance(run.get("content"), str):
                    parts.append(run["content"])
    return "".join(parts)


def list_document_blocks(
    *,
    description: str,
    name: str = "list_document_blocks",
    locale: str = "zh-CN",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：按文档顺序列出 docx 文档的块（`block_id` / `block_type` / 文本），返回一个 [feishu.agent.tools.Tool][]。

    调用 `client.docx.list_blocks(document_id)`，为每个块提取稳定的 `block_id`、`block_type` 与可读文本，供模型
    定位要用 [feishu.agent.toolkit.content.update_document][] 改写的具体块。仅返回块的结构与文本，不含其余原始字段。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"list_document_blocks"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        as_user: 是否以请求用户身份读取。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = list_document_blocks(description="列出文档块")
        >>> tool.name, tool.requires_approval
        ('list_document_blocks', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "document id"},
            "max_items": {"type": "integer", "description": "max blocks to return"},
        },
        "required": ["document_id"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        blocks = await client.docx.list_blocks(arguments["document_id"], max_items=arguments.get("max_items"))
        summary = [
            {"block_id": block.get("block_id"), "block_type": block.get("block_type"), "text": _block_text(block)}
            for block in blocks
        ]
        return ToolResult(ToolOutcome.COMPLETED, content=summary)

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)


def update_document(
    *,
    description: str,
    name: str = "update_document",
    locale: str = "zh-CN",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：改写 docx 文档中某个文本块的内容，返回一个需审批的 [feishu.agent.tools.Tool][]。

    真正的「更新」（区别于 [feishu.agent.toolkit.content.append_to_document][] 的追加）：处理函数以新文本重建该
    块的文本元素，调用 `client.docx.patch_block(document_id, block_id, {"update_text_elements": ...})` 覆盖指定
    块（按 `block_id`）的全部文本。`block_id` 由 [feishu.agent.toolkit.content.list_document_blocks][] 获取；仅
    适用于文本类块。`requires_approval=True` 时由 [feishu.agent.loop.Agent][] 先发审批卡片，用户批准后才执行写入。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"update_document"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        requires_approval: 是否需用户审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = update_document(description="改写文档某个块")
        >>> tool.name, tool.requires_approval
        ('update_document', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "document id"},
            "block_id": {
                "type": "string",
                "description": "id of the text block to overwrite (from list_document_blocks)",
            },
            "text": {"type": "string", "description": "new text content that replaces the block's text"},
        },
        "required": ["document_id", "block_id", "text"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        update = {"update_text_elements": {"elements": [{"text_run": {"content": arguments["text"]}}]}}
        result = await client.docx.patch_block(arguments["document_id"], arguments["block_id"], update)
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def delete_document(
    *,
    description: str,
    name: str = "delete_document",
    locale: str = "zh-CN",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：将 docx 文档移入回收站，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数调用 `client.drive.files.delete(document_id, type="docx")`；删除后文档进入回收站。
    `requires_approval=True` 时由 [feishu.agent.loop.Agent][] 先发审批卡片，用户批准后处理函数才执行删除。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"delete_document"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        requires_approval: 是否需用户审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份删除。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = delete_document(description="删除文档")
        >>> tool.name, tool.requires_approval
        ('delete_document', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "document id"},
        },
        "required": ["document_id"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.drive.files.delete(arguments["document_id"], type="docx")
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def append_to_sheet(
    *,
    description: str,
    name: str = "append_to_sheet",
    locale: str = "zh-CN",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：在电子表格指定区域之后追加行数据，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数调用 `client.sheets.append_range(spreadsheet_token, range, values)`；`range` 形如
    `<sheetId>!<起始位置>:<结束位置>`，`values` 为二维数组（外层为行、内层为列）。飞书会在 `range`
    所在区域之后自动寻找空行并追加。`requires_approval=True` 时由 [feishu.agent.loop.Agent][] 先发审批卡片，
    用户批准后处理函数才执行写入。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"append_to_sheet"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        requires_approval: 是否需用户审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = append_to_sheet(description="追加表格行")
        >>> tool.name, tool.requires_approval
        ('append_to_sheet', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "spreadsheet_token": {"type": "string", "description": "spreadsheet token"},
            "range": {
                "type": "string",
                "description": "location range, e.g. <sheetId>!<start>:<end>",
            },
            "values": {
                "type": "array",
                "description": "2D array of rows to append (outer rows, inner columns)",
                "items": {"type": "array", "items": {}},
            },
        },
        "required": ["spreadsheet_token", "range", "values"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.sheets.append_range(
            arguments["spreadsheet_token"],
            arguments["range"],
            arguments["values"],
        )
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def update_sheet_range(
    *,
    description: str,
    name: str = "update_sheet_range",
    locale: str = "zh-CN",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：覆盖电子表格指定区域的单元格数据，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数调用 `client.sheets.write_range(spreadsheet_token, range, values)`；`range` 形如
    `<sheetId>!<起始位置>:<结束位置>`，`values` 为二维数组（外层为行、内层为列）。该接口直接覆盖 `range`
    区域的现有内容。`requires_approval=True` 时由 [feishu.agent.loop.Agent][] 先发审批卡片，用户批准后处理函数才执行写入。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"update_sheet_range"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        requires_approval: 是否需用户审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = update_sheet_range(description="覆盖表格区域")
        >>> tool.name, tool.requires_approval
        ('update_sheet_range', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "spreadsheet_token": {"type": "string", "description": "spreadsheet token"},
            "range": {
                "type": "string",
                "description": "range to overwrite, e.g. <sheetId>!<start>:<end>",
            },
            "values": {
                "type": "array",
                "description": "2D array of rows to write (outer rows, inner columns)",
                "items": {"type": "array", "items": {}},
            },
        },
        "required": ["spreadsheet_token", "range", "values"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.sheets.write_range(
            arguments["spreadsheet_token"],
            arguments["range"],
            arguments["values"],
        )
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def delete_sheet_rows(
    *,
    description: str,
    name: str = "delete_sheet_rows",
    locale: str = "zh-CN",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：删除电子表格中指定区间的若干行，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数调用 `client.sheets.delete_dimension(spreadsheet_token, sheet_id, major_dimension="ROWS",
    start_index=..., end_index=...)`。`start_index` 与 `end_index` 按飞书约定为 1 起始且首尾均包含（删除区间为
    `[start_index, end_index]`）。`requires_approval=True` 时由 [feishu.agent.loop.Agent][] 先发审批卡片，
    用户批准后处理函数才执行删除。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"delete_sheet_rows"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        requires_approval: 是否需用户审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份删除。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = delete_sheet_rows(description="删除表格行")
        >>> tool.name, tool.requires_approval
        ('delete_sheet_rows', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "spreadsheet_token": {"type": "string", "description": "spreadsheet token"},
            "sheet_id": {"type": "string", "description": "sheet id"},
            "start_index": {
                "type": "integer",
                "description": "first row to delete (1-based, inclusive per Feishu)",
            },
            "end_index": {
                "type": "integer",
                "description": "last row to delete (1-based, inclusive per Feishu)",
            },
        },
        "required": ["spreadsheet_token", "sheet_id", "start_index", "end_index"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.sheets.delete_dimension(
            arguments["spreadsheet_token"],
            arguments["sheet_id"],
            major_dimension="ROWS",
            start_index=arguments["start_index"],
            end_index=arguments["end_index"],
        )
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def read_sheet_range(
    *,
    description: str,
    name: str = "read_sheet_range",
    locale: str = "zh-CN",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：读取电子表格单个区域的单元格数据，返回一个 [feishu.agent.tools.Tool][]。

    处理函数调用 `client.sheets.read_range(spreadsheet_token, range, value_render_option=...)`；`range` 形如
    `<sheetId>!<起始位置>:<结束位置>`（例如 `Q7PlXT!A1:B2`），`value_render_option` 可选
    `ToString`、`Formula`、`FormattedValue`、`UnformattedValue`，缺省取接口默认。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"read_sheet_range"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        as_user: 是否以请求用户身份读取。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = read_sheet_range(description="读取表格区域")
        >>> tool.name, tool.requires_approval
        ('read_sheet_range', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "spreadsheet_token": {"type": "string", "description": "spreadsheet token"},
            "range": {
                "type": "string",
                "description": "range to read, e.g. <sheetId>!<start>:<end>",
            },
            "value_render_option": {
                "type": "string",
                "enum": ["ToString", "Formula", "FormattedValue", "UnformattedValue"],
                "description": "cell value rendering option; defaults to the API default",
            },
        },
        "required": ["spreadsheet_token", "range"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.sheets.read_range(
            arguments["spreadsheet_token"],
            arguments["range"],
            value_render_option=arguments.get("value_render_option"),
        )
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)
