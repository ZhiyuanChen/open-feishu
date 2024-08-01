# open-feishu
# Copyright (C) 2024-Present  Zhiyuan Chen

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import annotations

from attrs import define


@define
class FeishuException(Exception):
    r"""
    通用异常

    飞书文档:
        [通用错误码](https://open.feishu.cn/document/server-docs/api-call-guide/generic-error-code)
    """

    code: int
    message: str

    def __repr__(self):
        return f"FeishuException(code={self.code}, message={self.message})"
