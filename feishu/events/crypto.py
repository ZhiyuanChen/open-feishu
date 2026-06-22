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

import base64
import hashlib

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

from ..errors import FeishuCryptoError


def decrypt(encrypt_key: str, b64_ciphertext: str) -> bytes:
    r"""
    解密飞书事件的 `encrypt` 密文。

    飞书在开启加密推送后，会将事件体加密为一段 Base64 字符串放在 `encrypt` 字段中。
    其算法为 AES-256-CBC：密钥取 `sha256(encrypt_key)`，前 16 字节为 IV，采用 PKCS7 填充。

    Args:
        encrypt_key: 应用在飞书开放平台配置的 Encrypt Key。
        b64_ciphertext: `encrypt` 字段中的 Base64 密文。

    Returns:
        解密并去除填充后的原始明文字节（通常是一段 JSON）。

    Raises:
        [feishu.errors.FeishuCryptoError][]: 当 Base64 非法、密文长度不足一个 AES 块，
            或密钥错误导致去填充失败时抛出。

    飞书文档:
        [订阅事件](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscriptions/encrypt-key-encryption-configuration-case)

    Examples:
        >>> decrypt("test key", "P37w+VZImNgPEO1RBhJ6RtKl7n6zymIbEG1pReEzghk=")
        b'hello world'
        >>> try:
        ...     decrypt("test key", "!!!not base64!!!")
        ... except FeishuCryptoError as exc:
        ...     print(type(exc).__name__)
        FeishuCryptoError
    """
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    try:
        blob = base64.b64decode(b64_ciphertext)
    except ValueError as exc:
        raise FeishuCryptoError(-1, f"invalid base64 ciphertext: {exc}") from exc
    if len(blob) < 16:
        raise FeishuCryptoError(-1, "ciphertext shorter than one AES block")
    iv, ct = blob[:16], blob[16:]
    try:
        plain = AES.new(key, AES.MODE_CBC, iv).decrypt(ct)
        return unpad(plain, AES.block_size)
    except ValueError as exc:
        raise FeishuCryptoError(-1, f"decrypt/unpad failed: {exc}") from exc
