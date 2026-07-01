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


class FilesNamespace(Namespace):
    r"""
    文件接口命名空间。

    通过 `client.drive.files` 访问，封装飞书云空间（Drive）中文件相关的服务端接口，包括列举、复制、移动、
    删除文件，批量查询文件元信息，新建文件夹，以及上传、下载文件与文档导出等能力。

    通常无需直接实例化，应通过 `client.drive.files` 访问。

    飞书文档:
        [云空间概述](https://open.feishu.cn/document/server-docs/docs/drive-v1/introduction)
    """

    async def copy(
        self,
        file_token: str,
        name: str,
        *,
        type: str,
        folder_token: str,
        user_id_type: str | None = None,
        **opts: Any,
    ) -> NestedDict:
        r"""
        复制文件。

        将指定文件复制到目标文件夹下。`type` 为源文件类型，`folder_token` 为目标文件夹 token。
        仅将显式传入的字段写入请求体，额外的关键字参数（`opts`）中值为 `None` 的项会被忽略，
        其余项原样并入请求体（如 `extra`）。

        Args:
            file_token: 源文件 token。
            name: 复制后的新文件名称。
            type: 源文件类型，例如 `doc`、`docx`、`sheet`、`bitable`、`file` 等。
            folder_token: 目标文件夹 token。
            user_id_type: 返回的用户 ID 类型，例如 `open_id`；为空时省略该查询参数。
            **opts: 其他复制参数，例如 `extra`；值为 `None` 时忽略。

        Returns:
            复制结果数据，含 `file` 字段（其中含新文件的 `token`、`name`、`type`、`url` 等）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [复制文件](https://open.feishu.cn/document/server-docs/docs/drive-v1/file/copy)
            参见 [feishu.drive.files.FilesNamespace.move][]。

        Examples:
            >>> await client.drive.files.copy(
            ...     "boxcnxxx", "Copy", type="file", folder_token="fldcnxxx"
            ... )  # doctest:+SKIP
            {'file': {'token': 'boxcnyyy', 'name': 'Copy', 'type': 'file', ...}}  # noqa: E501
        """
        params = {"user_id_type": user_id_type}
        body = {"name": name, "type": type, "folder_token": folder_token}
        body.update({k: v for k, v in opts.items() if v is not None})
        return await self._request_data(
            "POST", f"drive/v1/files/{quote_segment(file_token)}/copy", params=params, json=body
        )

    async def create_export_task(
        self, token: str, file_extension: str, *, type: str, sub_id: str | None = None
    ) -> NestedDict:
        r"""
        创建文档导出任务。

        创建一个将云文档导出为其他格式（如将 `docx` 导出为 `docx`/`pdf`，`sheet` 导出为 `xlsx`/`csv`）
        的异步任务。任务创建后通过 [feishu.drive.files.FilesNamespace.get_export_task][] 轮询其状态，
        完成后再以 [feishu.drive.files.FilesNamespace.download_export][] 下载导出产物。

        Args:
            token: 待导出的云文档 token。
            file_extension: 导出的目标文件扩展名，例如 `docx`、`pdf`、`xlsx`、`csv`。
            type: 待导出文档的类型，例如 `doc`、`docx`、`sheet`、`bitable`。
            sub_id: 子表 ID；导出电子表格中的某个工作表或多维表格中的某个数据表时使用，否则为空。

        Returns:
            创建结果数据，含 `ticket` 字段（用于查询导出任务结果）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [创建导出任务](https://open.feishu.cn/document/server-docs/docs/drive-v1/export_task/create)
            参见 [feishu.drive.files.FilesNamespace.get_export_task][]。

        Examples:
            >>> await client.drive.files.create_export_task(
            ...     "doxcabc", "pdf", type="docx"
            ... )  # doctest:+SKIP
            {'ticket': '6933093124755423251'}
        """
        body = {"file_extension": file_extension, "token": token, "type": type}
        if sub_id is not None:
            body["sub_id"] = sub_id
        return await self._request_data("POST", "drive/v1/export_tasks", json=body)

    async def create_folder(self, name: str, folder_token: str) -> NestedDict:
        r"""
        新建文件夹。

        在指定父文件夹下新建一个空文件夹。`folder_token` 为父文件夹 token；为空字符串时
        在云空间根目录下创建。

        Args:
            name: 新建文件夹的名称。
            folder_token: 父文件夹 token；为空字符串时在云空间根目录下创建。

        Returns:
            新建结果数据，含 `token`（新文件夹 token）与 `url`（新文件夹访问链接）字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [新建文件夹](https://open.feishu.cn/document/server-docs/docs/drive-v1/folder/create_folder)
            参见 [feishu.drive.files.FilesNamespace.list][]。

        Examples:
            >>> await client.drive.files.create_folder("New", "fldcnxxx")  # doctest:+SKIP
            {'token': 'fldcnyyy', 'url': 'https://example.feishu.cn/drive/folder/fldcnyyy'}  # noqa: E501
        """
        body = {"name": name, "folder_token": folder_token}
        return await self._request_data("POST", "drive/v1/files/create_folder", json=body)

    async def delete(self, file_token: str, *, type: str) -> NestedDict:
        r"""
        删除文件或文件夹。

        删除指定文件（或文件夹）。删除后文件会进入回收站。`type` 为被删除对象的类型，作为查询参数发送。

        Args:
            file_token: 被删除的文件（或文件夹）token。
            type: 被删除对象的类型，例如 `file`、`docx`、`sheet`、`bitable`、`folder` 等。

        Returns:
            删除结果数据，含异步任务 `task_id`（删除文件夹等耗时操作时返回）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [删除文件或文件夹](https://open.feishu.cn/document/server-docs/docs/drive-v1/file/delete)
            参见 [feishu.drive.files.FilesNamespace.move][]。

        Examples:
            >>> await client.drive.files.delete("boxcnxxx", type="file")  # doctest:+SKIP
            {'task_id': '12345'}
        """
        return await self._request_data("DELETE", f"drive/v1/files/{quote_segment(file_token)}", params={"type": type})

    async def download(self, file_token: str) -> bytes:
        r"""
        下载云空间文件。

        以二进制形式下载指定文件的原始内容。

        Args:
            file_token: 文件 token。

        Returns:
            文件内容的原始字节。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [下载文件](https://open.feishu.cn/document/server-docs/docs/drive-v1/download/download)
            参见 [feishu.drive.files.FilesNamespace.upload][]。

        Examples:
            >>> await client.drive.files.download("boxcnxxx")  # doctest:+SKIP
            b'...'
        """
        return await self._client.download(f"drive/v1/files/{quote_segment(file_token)}/download")

    async def download_export(self, file_token: str) -> bytes:
        r"""
        下载导出产物。

        以二进制形式下载导出任务生成的文件。`file_token` 取自
        [feishu.drive.files.FilesNamespace.get_export_task][] 返回的 `result.file_token`。

        Args:
            file_token: 导出产物的文件 token。

        Returns:
            导出文件内容的原始字节。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [下载导出文件](https://open.feishu.cn/document/server-docs/docs/drive-v1/export_task/download)
            参见 [feishu.drive.files.FilesNamespace.get_export_task][]。

        Examples:
            >>> await client.drive.files.download_export("boxcnxxx")  # doctest:+SKIP
            b'...'
        """
        return await self._client.download(f"drive/v1/export_tasks/file/{quote_segment(file_token)}/download")

    async def get_export_task(self, ticket: str, *, token: str) -> NestedDict:
        r"""
        查询导出任务结果。

        通过创建导出任务时返回的 `ticket` 轮询其状态；当 `result.job_status` 为 0 时表示导出完成，
        此时可使用 `result.file_token` 调用 [feishu.drive.files.FilesNamespace.download_export][] 下载产物。

        Args:
            ticket: 导出任务的票据，由 [feishu.drive.files.FilesNamespace.create_export_task][] 返回。
            token: 待导出的云文档 token（与创建任务时一致），作为查询参数发送。

        Returns:
            查询结果数据，含 `result` 字段（其中含 `file_token`、`file_name`、`file_size`、
            `job_status`、`job_error_msg`、`type` 等字段）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [查询导出任务结果](https://open.feishu.cn/document/server-docs/docs/drive-v1/export_task/get)
            参见 [feishu.drive.files.FilesNamespace.download_export][]。

        Examples:
            >>> await client.drive.files.get_export_task(
            ...     "6933093124755423251", token="doxcabc"
            ... )  # doctest:+SKIP
            {'result': {'file_token': 'boxcnxxx', 'job_status': 0, ...}}  # noqa: E501
        """
        return await self._request_data(
            "GET", f"drive/v1/export_tasks/{quote_segment(ticket)}", params={"token": token}
        )

    async def batch_query_metas(
        self, request_docs: list[dict[str, Any]], *, with_url: bool = False, user_id_type: str | None = None
    ) -> NestedDict:
        r"""
        批量查询文件元信息。

        在一次请求中查询多个文件的元信息。`request_docs` 为查询条目列表，每个元素需包含
        `doc_token`（文件 token）与 `doc_type`（文件类型，如 `doc`、`docx`、`sheet`、`bitable`、
        `file` 等）。

        Args:
            request_docs: 查询条目列表，每个元素形如 `{"doc_token": "doxcabc", "doc_type": "docx"}`。
            with_url: 是否返回文件的访问链接（`url` 字段），默认为 `False`。
            user_id_type: 返回的用户 ID 类型，例如 `open_id`；为空时省略该查询参数。

        Returns:
            元信息查询结果数据，含 `metas` 列表（每项含 `doc_token`、`doc_type`、`title`、
            `owner_id`、`latest_modify_time`、`url` 等字段）与 `failed_list`（查询失败的条目）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取文件元数据](https://open.feishu.cn/document/server-docs/docs/drive-v1/file/batch_query)
            参见 [feishu.drive.files.FilesNamespace.list][]。

        Examples:
            >>> docs = [{"doc_token": "doxcabc", "doc_type": "docx"}]
            >>> await client.drive.files.batch_query_metas(docs, with_url=True)  # doctest:+SKIP
            {'metas': [{'doc_token': 'doxcabc', 'doc_type': 'docx', ...}], 'failed_list': []}  # noqa: E501
        """
        params = {"user_id_type": user_id_type}
        body = {"request_docs": request_docs, "with_url": with_url}
        return await self._request_data("POST", "drive/v1/metas/batch_query", params=params, json=body)

    async def list(
        self,
        *,
        folder_token: str | None = None,
        page_size: int = 50,
        max_items: int | None = None,
        **opts: Any,
    ) -> list[NestedDict]:
        r"""
        获取文件夹下的文件清单。

        自动翻页并将各页结果拼接为单个列表返回。`folder_token` 为空时返回用户云空间根目录下的
        文件清单。`page_size` 会被限制在 [feishu.consts.MAX_PAGE_SIZE][] 以内。额外的关键字参数
        （`opts`）中值为 `None` 的项会被忽略，其余项原样并入查询参数（如 `order_by`、`direction`、
        `user_id_type` 等）。

        Args:
            folder_token: 文件夹 token；为空时返回云空间根目录下的文件清单。
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。
            **opts: 其他查询参数，例如 `order_by`、`direction`、`user_id_type`；值为 `None` 时忽略。

        Returns:
            文件数据列表，每项含 `token`、`name`、`type`、`parent_token`、`url` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取文件夹下的清单](https://open.feishu.cn/document/server-docs/docs/drive-v1/folder/list)
            参见 [feishu.drive.files.FilesNamespace.batch_query_metas][]。

        Examples:
            >>> await client.drive.files.list(folder_token="fldcnxxx")  # doctest:+SKIP
            [{'token': 'doxcabc', 'name': 'a', 'type': 'docx', ...}, {'token': 'shtxxx', ...}]  # noqa: E501
        """
        params = {"folder_token": folder_token}
        params.update({k: v for k, v in opts.items() if v is not None})
        return await self._client.paginate_get(
            "drive/v1/files",
            params=params,
            page_size=page_size,
            max_items=max_items,
            map_page=_remap_files_page,
        )

    async def move(self, file_token: str, *, folder_token: str, type: str) -> NestedDict:
        r"""
        移动文件或文件夹。

        将指定文件（或文件夹）移动到目标文件夹下。`type` 为被移动对象的类型，`folder_token`
        为目标文件夹 token。

        Args:
            file_token: 被移动的文件（或文件夹）token。
            folder_token: 目标文件夹 token。
            type: 被移动对象的类型，例如 `file`、`docx`、`sheet`、`bitable`、`folder` 等。

        Returns:
            移动结果数据，含异步任务 `task_id`（移动文件夹等耗时操作时返回）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [移动文件或文件夹](https://open.feishu.cn/document/server-docs/docs/drive-v1/file/move)
            参见 [feishu.drive.files.FilesNamespace.copy][]。

        Examples:
            >>> await client.drive.files.move(
            ...     "boxcnxxx", folder_token="fldcnxxx", type="file"
            ... )  # doctest:+SKIP
            {'task_id': '12345'}
        """
        body = {"type": type, "folder_token": folder_token}
        return await self._request_data("POST", f"drive/v1/files/{quote_segment(file_token)}/move", json=body)

    async def upload(
        self,
        file_name: str,
        parent_node: str,
        file: bytes,
        *,
        parent_type: str = "explorer",
        size: int | None = None,
        **opts: Any,
    ) -> NestedDict:
        r"""
        上传文件到云空间。

        以 `multipart/form-data` 方式将文件内容一次性上传至云空间（适用于不超过 20 MB 的文件）。
        `file` 为文件的原始字节，`parent_node` 为目标文件夹 token，`parent_type` 固定为 `explorer`。
        `size` 为空时按 `file` 的字节长度自动计算。额外的关键字参数（`opts`）中值为 `None` 的项会被
        忽略，其余项原样并入表单字段（如 `checksum`）。

        Args:
            file_name: 上传后的文件名称（含扩展名）。
            parent_node: 目标文件夹 token。
            file: 文件的原始字节内容。
            parent_type: 上传点类型，云空间上传固定为 `explorer`。
            size: 文件字节大小；为空时按 `file` 的长度自动计算。
            **opts: 其他表单字段，例如 `checksum`（Adler-32 校验和）；值为 `None` 时忽略。

        Returns:
            上传结果数据，含 `file_token` 字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [上传文件](https://open.feishu.cn/document/server-docs/docs/drive-v1/upload/upload_all)
            参见 [feishu.drive.files.FilesNamespace.download][]。

        Examples:
            >>> await client.drive.files.upload(
            ...     "a.txt", "fldcnxxx", b"hello"
            ... )  # doctest:+SKIP
            {'file_token': 'boxcnxxx'}
        """
        data = {
            "file_name": file_name,
            "parent_type": parent_type,
            "parent_node": parent_node,
            "size": size if size is not None else len(file),
        }
        data.update({k: v for k, v in opts.items() if v is not None})
        envelope = await self._client.upload("drive/v1/files/upload_all", data=data, files={"file": file})
        return _data(envelope)


def _remap_files_page(envelope: NestedDict) -> NestedDict:
    r"""
    将清单接口的分页字段归一化为分页助手期望的形态。

    文件清单接口使用 `files` / `next_page_token`，而 [feishu.client.FeishuClient.paginate_get][]
    期望 `items` / `page_token`。本助手将信封改写为 `{"data": {"items": ..., "has_more": ...,
    "page_token": ...}}`。

    Args:
        envelope: 清单接口返回的原始响应信封。

    Returns:
        改写后的响应信封。
    """
    data = envelope["data"]
    return NestedDict(
        {
            "data": {
                "items": data.get("files", []),
                "has_more": data.get("has_more"),
                "page_token": data.get("next_page_token"),
            }
        }
    )
