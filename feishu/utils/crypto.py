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

import base64
import hashlib

from chanfig import NestedDict
from Crypto.Cipher import AES

from feishu import variables


class AESCipher:
    r"""
    AES 加密/解密工具类

    Args:
        key: 用于加密/解密的密钥

    飞书文档:
        [配置 Encrypt Key](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/configure-encrypt-key)

    Examples:
        >>> cypher = AESCipher("test key")
        >>> encrypt = "P37w+VZImNgPEO1RBhJ6RtKl7n6zymIbEG1pReEzghk="
        >>> cypher.decrypt(encrypt)
        'hello world'
    """  # noqa: E501

    def __init__(self, key: str):
        self.bs = AES.block_size
        self.key = hashlib.sha256(AESCipher.str_to_bytes(key)).digest()

    @staticmethod
    def str_to_bytes(data: str) -> bytes:
        u_type = type(b"".decode("utf8"))
        if isinstance(data, u_type):
            return data.encode("utf8")
        return data  # type: ignore[return-value]

    @staticmethod
    def _unpad(s: bytes) -> bytes:
        return s[: -ord(s[len(s) - 1 :])]

    def decrypt(self, enc: str | bytes) -> str | bytes:
        if isinstance(enc, str):
            return self._decrypt(base64.b64decode(enc)).decode("utf8")
        if isinstance(enc, bytes):
            return self._decrypt(enc)
        raise ValueError("Invalid type")

    def _decrypt(self, enc: bytes) -> bytes:
        iv = enc[: AES.block_size]
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return self._unpad(cipher.decrypt(enc[AES.block_size :]))


def decrypt(request: str | dict) -> NestedDict:
    r"""
    解密飞书请求

    Args:
        request: 飞书请求

    飞书文档:
        [配置 Encrypt Key](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/configure-encrypt-key)
    """  # noqa: E501
    if isinstance(request, str):
        encrypted = request
    if isinstance(request, dict):
        encrypted = request["encrypt"]
    return NestedDict.from_jsons(AESCipher(variables.ENCRYPT_KEY).decrypt(encrypted))  # type: ignore[arg-type]
