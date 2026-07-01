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

import mimetypes
from collections.abc import Mapping
from typing import Any

from chanfig import NestedDict

from .._envelope import _data
from .._namespace import Namespace


def approval_file_type_for_media_type(media_type: str | None) -> str:
    r"""
    根据 MIME 类型返回飞书审批文件类型。

    图片 MIME 类型返回 `"image"`，其他类型返回 `"attachment"`，可直接传给
    [feishu.approval.files.FilesNamespace.upload][] 的 `file_type` 参数。
    """
    return "image" if (media_type or "").startswith("image/") else "attachment"


def approval_file_code(upload_response: Mapping[str, Any]) -> str | None:
    r"""
    从已知上传响应结构中提取审批文件 `code`。

    飞书审批文件上传可能直接在顶层返回 `code`，也可能在 `urls_detail[*].code` 中返回。
    未找到可用 `code` 时返回 `None`。
    """
    code = upload_response.get("code")
    if isinstance(code, str) and code:
        return code
    urls_detail = upload_response.get("urls_detail")
    if isinstance(urls_detail, list):
        for detail in urls_detail:
            if not isinstance(detail, Mapping):
                continue
            code = detail.get("code")
            if isinstance(code, str) and code:
                return code
    return None


def normalize_approval_file_upload_response(
    upload_response: Mapping[str, Any],
    *,
    file_type: str | None = None,
    media_type: str | None = None,
) -> NestedDict:
    r"""
    将审批文件上传响应归一化为小型状态对象。

    成功时返回 `status="uploaded"` 与 `code`；失败时保留原始上传响应，便于调用方诊断
    [feishu.approval.files.FilesNamespace.upload][] 未返回文件 `code` 的原因。
    """
    code = approval_file_code(upload_response)
    if code:
        data = NestedDict(status="uploaded", code=code)
        if file_type:
            data.file_type = file_type
        if media_type:
            data.media_type = media_type
        if upload_response.get("code") != code:
            data.approval_file_upload_response = upload_response
        return data
    return NestedDict(
        status="upload_failed",
        error="approval file upload did not return code",
        approval_file_upload_response=upload_response,
    )


class FilesNamespace(Namespace):
    r"""
    审批文件接口命名空间。

    通过 `client.approval.files` 访问，用于将图片或附件上传到审批系统，返回的 `code` 可填入
    审批实例表单里的 image / attachmentV2 控件。
    """

    async def upload(
        self,
        content: bytes,
        *,
        file_name: str | None = None,
        file_type: str = "attachment",
        media_type: str | None = None,
        **extra: Any,
    ) -> NestedDict:
        r"""
        上传审批图片或附件。

        Args:
            content: 文件字节。
            file_name: 文件名。为空时使用 `attachment`。
            file_type: 审批文件类型，常见为 `attachment` 或 `image`。
            media_type: MIME 类型；为空时按文件名猜测。
            **extra: 其他普通 multipart 字段。

        Returns:
            上传结果数据，通常含 `code` 字段。
        """
        name = file_name or "attachment"
        media_type = media_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        data = {"type": file_type, "name": name, **{key: value for key, value in extra.items() if value is not None}}
        # Approval file upload is a SPECIAL endpoint: NOT /open-apis/approval/v4/..., but the legacy approval host
        # `https://www.feishu.cn/approval/openapi/v2/file/upload` (tenant token + multipart; standard
        # {code,msg,data:{code}} envelope). Derive the host from base_url (open.* -> www.*) so Feishu and Lark
        # regions both resolve correctly. See https://open.feishu.cn/document/server-docs/approval-v4/file/upload-files
        upload_url = self._client.base_url.replace("//open.", "//www.").rstrip("/") + "/approval/openapi/v2/file/upload"
        envelope = await self._client.upload(
            upload_url,
            data=data,
            files={"content": (name, content, media_type)},
        )
        return _data(envelope)
