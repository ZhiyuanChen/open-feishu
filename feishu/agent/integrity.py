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

import hashlib
import json
import uuid
from typing import Any

# A stable, project-agnostic UUID namespace for deriving deterministic ids.
# Callers further qualify their seeds with their own `namespace` (e.g. a product
# name) so two products never collide in the same id space.
_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/ZhiyuanChen/open-feishu#agent.integrity")


def _canonical_json(value: Any) -> str:
    r"""
    将任意可 JSON 化的值序列化为规范化、可复现的紧凑 JSON 字符串。

    键按字典序排序、去除多余空白、保留非 ASCII 字符，并以 `str` 兜底处理无法直接序列化的对象，
    确保「语义相同的值产生逐字节相同的字符串」。
    """
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def stable_hash(value: Any) -> str:
    r"""
    计算任意可 JSON 化值的稳定 SHA-256 摘要（十六进制）。

    先经 [_canonical_json][feishu.agent.integrity] 规范化再哈希，因而与字典键顺序无关：语义相同的两个
    值得到相同摘要。这是审批防篡改校验、幂等键派生与执行结果缓存键的共同基石。

    Args:
        value: 任意可 JSON 序列化的值。

    Returns:
        64 个十六进制字符的 SHA-256 摘要。

    Examples:
        >>> stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})
        True
        >>> len(stable_hash({"a": 1}))
        64
    """
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def payload_sha256(payload: Any) -> str:
    r"""
    计算写操作负载的稳定 SHA-256 摘要，是 [feishu.agent.integrity.stable_hash][] 的语义别名。

    审批卡片回传时携带创建时记录的摘要；执行前重新计算并比对，二者不一致即判定负载在「展示—确认」之间
    被篡改，从而拒绝执行。

    Args:
        payload: 写操作负载。

    Returns:
        负载的 64 位十六进制 SHA-256 摘要。

    Examples:
        >>> payload_sha256({"env": "prod"}) == stable_hash({"env": "prod"})
        True
    """
    return stable_hash(payload)


def payload_summary(payload: Any, *, include_hash: bool = False, max_keys: int = 50) -> dict[str, Any]:
    r"""
    生成写操作负载的结构化摘要，用于审计与卡片预览而不泄露完整内容。

    仅记录类型、键数量、前若干个键名或元素个数等结构信息；`include_hash=True` 时附带稳定摘要。

    Args:
        payload: 写操作负载。
        include_hash: 是否附带 [feishu.agent.integrity.stable_hash][] 摘要。默认为 `False`。
        max_keys: 字典摘要中保留的键名上限。默认为 `50`。

    Returns:
        含 `type` 及（视类型而定）`key_count`/`keys`/`item_count`/`sha256` 的结构化摘要。

    Examples:
        >>> summary = payload_summary({"a": 1, "b": 2})
        >>> summary["type"], summary["key_count"], summary["keys"]
        ('dict', 2, ['a', 'b'])
        >>> payload_summary([1, 2, 3])["item_count"]
        3
    """
    summary: dict[str, Any] = {"type": type(payload).__name__}
    if isinstance(payload, dict):
        keys = [str(key) for key in payload]
        summary["key_count"] = len(keys)
        summary["keys"] = sorted(keys)[:max_keys]
    elif isinstance(payload, (list, tuple, set)):
        summary["item_count"] = len(payload)
    if include_hash:
        summary["sha256"] = stable_hash(payload)
    return summary


def derive_approval_id(*, scope: str, operation: str, idempotency_key: str, namespace: str = "feishu") -> str:
    r"""
    由作用域、操作名与幂等键确定性地派生审批 / 写请求 id。

    相同输入恒得到相同 id，因而「同一作用域内、同一操作、同一幂等键」的重复提议天然指向同一条审批记录，
    便于去重与覆盖（supersede）。`namespace` 由调用方（通常是产品名）提供，使不同产品的 id 空间互不重叠，
    而非在 SDK 内硬编码任何产品前缀。

    Args:
        scope: 交互作用域键，例如 `tenant:chat:user`。
        operation: 写操作名，例如 `calendar.events.create`。
        idempotency_key: 幂等键，见 [feishu.agent.integrity.derive_idempotency_key][]。
        namespace: 调用方命名空间，用于隔离不同产品的 id 空间。默认为 `"feishu"`。

    Returns:
        确定性的 UUID5 字符串。

    Examples:
        >>> a = derive_approval_id(scope="t:c:u", operation="op", idempotency_key="k", namespace="example")
        >>> b = derive_approval_id(scope="t:c:u", operation="op", idempotency_key="k", namespace="example")
        >>> a == b
        True
        >>> a == derive_approval_id(scope="t:c:u", operation="op", idempotency_key="k", namespace="other")
        False
    """
    seed = f"{namespace}:approval:{scope}:{operation}:{idempotency_key}"
    return str(uuid.uuid5(_ID_NAMESPACE, seed))


def derive_idempotency_key(*, message_id: str, payload_sha256: str, namespace: str = "feishu") -> str:
    r"""
    由触发消息 id 与负载摘要确定性地派生幂等键。

    将「哪条消息」与「何种负载」绑定为一个稳定键：同一条消息重复触发同一负载只会产生一次执行，而负载一旦
    变化幂等键随之改变。`namespace` 同样由调用方提供以隔离产品 id 空间。

    Args:
        message_id: 触发该写操作的飞书消息 id。
        payload_sha256: 负载的稳定摘要，见 [feishu.agent.integrity.payload_sha256][]。
        namespace: 调用方命名空间。默认为 `"feishu"`。

    Returns:
        确定性的 UUID5 字符串。

    Examples:
        >>> k = derive_idempotency_key(message_id="om_1", payload_sha256="abc", namespace="example")
        >>> k == derive_idempotency_key(message_id="om_1", payload_sha256="abc", namespace="example")
        True
    """
    seed = f"{namespace}:idempotency:{message_id}:{payload_sha256}"
    return str(uuid.uuid5(_ID_NAMESPACE, seed))


__all__ = [
    "derive_approval_id",
    "derive_idempotency_key",
    "payload_sha256",
    "payload_summary",
    "stable_hash",
]
