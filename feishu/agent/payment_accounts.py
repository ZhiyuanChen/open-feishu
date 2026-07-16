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
收款账户（payment account）解析：从用户本人历史审批实例中恢复账户控件值，句柄优先、内存暂存、严格按用户隔离。

飞书开放平台文档把 `account`（收款账户）列为创建审批实例 API 不直接支持的控件；但用户本人过去提交的报销实例
里会带完整账户控件值。这个模块只从请求用户本人历史实例读取账户值，向模型只暴露不可逆句柄和脱敏标签；完整
账户值仅保存在内存中，并只在提交瞬间还原进 `account` 控件。
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..approval import approval_account_label, approval_account_number, approval_account_widgets
from ..auth import user_identity_keys


def payment_account_keys(user: Mapping[str, Any]) -> tuple[str, ...]:
    r"""用户的稳定多别名键；[feishu.auth.user_tokens.user_identity_keys][] 的别名。"""
    return user_identity_keys(user)


def payment_account_handle(number: str) -> str:
    r"""把卡号映射为稳定、不可逆的句柄（`pa_<sha256[:16]>`）；模型只见句柄，绝不见卡号。"""
    return "pa_" + hashlib.sha256(number.encode("utf-8")).hexdigest()[:16]


def _looks_like_round_trip_dump(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_looks_like_round_trip_dump(item) for item in value.values())
    if not isinstance(value, str):
        return False
    text = value.strip()
    if text.startswith("map["):
        return True
    if not (text.startswith("[") and text.endswith("]")):
        return False
    body = text[1:-1].strip()
    return bool(body) and all(part.isdigit() for part in body.split())


def _is_reusable_account_value(value: Any) -> bool:
    return (
        isinstance(value, Mapping) and bool(approval_account_number(value)) and not _looks_like_round_trip_dump(value)
    )


@dataclass
class PaymentAccount:
    r"""一个收款账户：模型可见的句柄和脱敏标签，外加仅服务端可见的完整控件值。"""

    account_id: str
    label: str
    account_value: dict[str, Any]
    user_keys: tuple[str, ...] = ()

    def summary(self) -> dict[str, str]:
        r"""模型安全视图：仅句柄和脱敏标签，绝不含完整账户值或卡号。"""
        return {"account_id": self.account_id, "label": self.label}


def _instance_belongs_to(instance: Mapping[str, Any], user: Mapping[str, Any]) -> bool:
    r"""防御性校验：取回的实例确属请求用户本人，避免复用他人账户。"""
    if not isinstance(instance, Mapping):
        return False
    for kind in ("open_id", "user_id"):
        mine = user.get(kind)
        theirs = instance.get(kind)
        if mine and theirs and mine == theirs:
            return True
    return False


class PaymentAccountResolver:
    r"""
    从请求用户本人的历史审批实例解析收款账户，句柄优先、内存暂存、严格按用户隔离。

    `recent` 经 [feishu.approval.instances.InstancesNamespace.query][] 按用户 `open_id` 拉取本人实例，再抽取账户值、
    去重、生成句柄与脱敏标签；完整账户值仅缓存在内存。`resolve` 仅在提交瞬间把句柄还原为完整值，供
    [feishu.agent.toolkit.approvals.create_approval_instance][] 填入账户控件。

    Args:
        client: 飞书客户端，提供 `approval.instances.query` / `approval.instances.get` 以拉取并读取本人历史实例。
        lookback_ms: 历史实例的回溯窗口（毫秒），即 `query` 的 `start_time` 相对当前时刻的回看跨度。
            默认为 `730 * 24 * 3600 * 1000`，即约 730 天（约 2 年）的历史回溯窗口。
    """

    def __init__(self, client: Any, *, lookback_ms: int = 730 * 24 * 3600 * 1000) -> None:
        self._client = client
        self._lookback_ms = lookback_ms
        self._cache: dict[str, dict[str, PaymentAccount]] = {}

    async def recent(
        self, user: Mapping[str, Any], *, approval_code: str | None = None, limit: int = 10
    ) -> list[PaymentAccount]:
        r"""返回请求用户本人历史里出现过的去重收款账户（句柄 + 脱敏标签）。"""
        user_keys = payment_account_keys(user)
        open_id = user.get("open_id") if isinstance(user, Mapping) else None
        if not user_keys or not open_id:
            return []
        end = int(time.time() * 1000)
        start = end - self._lookback_ms
        try:
            items = await self._client.approval.instances.query(
                user_id=open_id,
                approval_code=approval_code,
                user_id_type="open_id",
                start_time=str(start),
                end_time=str(end),
                max_items=50,
            )
        except Exception:  # noqa: BLE001 - account discovery must not crash the tool; degrade to no results.
            logging.getLogger("feishu").debug("payment account query failed for the requesting user", exc_info=True)
            return []

        accounts: dict[str, PaymentAccount] = {}
        for item in items:
            instance_node = item.get("instance") if isinstance(item, Mapping) else None
            code = instance_node.get("code") if isinstance(instance_node, Mapping) else None
            if not code:
                continue
            try:
                instance = await self._client.approval.instances.get(code)
            except Exception:  # noqa: BLE001 - skip historical instances that cannot be read.
                logging.getLogger("feishu").debug(
                    "could not read instance %s for payment accounts", code, exc_info=True
                )
                continue
            if not _instance_belongs_to(instance, user):
                continue
            for widget in approval_account_widgets(instance):
                value = widget.get("value")
                if not _is_reusable_account_value(value):
                    logging.getLogger("feishu").debug(
                        "skipping non-reusable payment account value from instance %s", code
                    )
                    continue
                number = approval_account_number(value)
                if not number:
                    continue
                handle = payment_account_handle(number)
                if handle not in accounts:
                    accounts[handle] = PaymentAccount(
                        account_id=handle,
                        label=approval_account_label(value),
                        account_value=dict(value),
                        user_keys=user_keys,
                    )
                if len(accounts) >= limit:
                    break
            if len(accounts) >= limit:
                break
        self._cache.setdefault(user_keys[0], {}).update(accounts)
        return list(accounts.values())

    async def resolve(self, user: Mapping[str, Any], account_id: str) -> dict[str, Any] | None:
        r"""把句柄还原为完整账户控件值，严格限定为请求用户本人。"""
        user_keys = payment_account_keys(user)
        if not user_keys:
            return None
        primary = user_keys[0]
        account = self._cache.get(primary, {}).get(account_id)
        if account is None:
            await self.recent(user)
            account = self._cache.get(primary, {}).get(account_id)
        if account is None:
            return None
        if not set(user_keys) & set(account.user_keys):
            return None
        return dict(account.account_value)
