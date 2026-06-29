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
收款账户（payment account）解析：从用户**本人**历史审批实例中恢复其银行账户，句柄优先、内存暂存、严格按用户隔离。

飞书没有枚举用户绑定收款账户的接口，但用户本人过去提交的审批实例里带有完整的账户值（自包含对象，而非不可逆
令牌），可原样重新提交。本模块只读取**请求用户本人**的历史实例（经 [feishu.approval.instances][] 的 `query`
按其 open_id 过滤——绝不触及他人），抽取账户值，向模型只暴露**不可逆句柄 + 脱敏标签**；完整账户值仅存于内存
（绝不落盘），并只在提交瞬间由 [feishu.agent.payment_accounts.PaymentAccountResolver.resolve][] 重新带入。

因此即使模型被越权操控（jailbreak），它既看不到完整卡号，也无法触达他人的收款账户。详见 [feishu.agent][]。
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
    r"""用户的稳定多别名键；[feishu.auth.user_tokens.user_identity_keys][] 的别名（全库统一表示）。"""
    return user_identity_keys(user)


def payment_account_handle(number: str) -> str:
    r"""把卡号映射为稳定、不可逆的句柄（`pa_<sha256[:16]>`）；模型只见句柄，绝不见卡号。"""
    return "pa_" + hashlib.sha256(number.encode("utf-8")).hexdigest()[:16]


@dataclass
class PaymentAccount:
    r"""一个收款账户：模型可见的句柄 + 脱敏标签，外加仅服务端可见的完整控件值。"""

    account_id: str  # opaque handle (pa_...), model-safe — parallels SharedFile.file_id
    label: str  # privacy-masked, model-safe (e.g. "杭州银行 ****8383 (张三)")
    account_value: dict[str, Any]  # the full account widget value — server-side ONLY, never to the model
    user_keys: tuple[str, ...] = ()  # owning user's alias keys — parallels SharedFile.user_keys

    def summary(self) -> dict[str, str]:
        r"""模型安全视图：仅句柄 + 脱敏标签，绝不含完整账户值 / 卡号。"""
        return {"account_id": self.account_id, "label": self.label}


def _instance_belongs_to(instance: Mapping[str, Any], user: Mapping[str, Any]) -> bool:
    r"""防御性校验：取回的实例确属请求用户本人（绝不复用他人账户）。"""
    if not isinstance(instance, Mapping):
        return False
    for kind in ("open_id", "user_id", "union_id"):
        mine = user.get(kind)
        theirs = instance.get(kind)
        if mine and theirs and mine == theirs:
            return True
    return False


class PaymentAccountResolver:
    r"""
    从「请求用户本人」的历史审批实例解析其收款账户，句柄优先、内存暂存、严格按用户隔离。

    `recent` 经 [feishu.approval.instances.InstancesNamespace.query][] 仅按用户 `open_id` 拉取其本人实例，
    再抽取账户值、去重、生成句柄与脱敏标签；完整账户值仅缓存在内存（绝不落盘）。`resolve` 仅在提交瞬间把句柄
    还原为完整值，供 [feishu.agent.toolkit.approvals.create_approval_instance][] 填入账户控件。模型自始至终
    只接触句柄与脱敏标签。命名与 [feishu.agent.shared_files.SharedFileResolver][] 对齐（`recent` / `user_keys`）。
    """

    def __init__(self, client: Any, *, lookback_ms: int = 730 * 24 * 3600 * 1000) -> None:
        self._client = client
        self._lookback_ms = lookback_ms
        # user_key -> {account_id: PaymentAccount}; full values live here in memory only, never persisted.
        self._cache: dict[str, dict[str, PaymentAccount]] = {}

    async def recent(
        self, user: Mapping[str, Any], *, approval_code: str | None = None, limit: int = 10
    ) -> list[PaymentAccount]:
        r"""返回请求用户本人历史里出现过的去重收款账户（句柄 + 脱敏标签）；无 `open_id` 时拒绝（绝不枚举他人）。"""
        user_keys = payment_account_keys(user)
        open_id = user.get("open_id") if isinstance(user, Mapping) else None
        if not user_keys or not open_id:
            # Without the user's own open_id we cannot scope the query to them — refuse rather than over-read.
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
        except Exception:  # noqa: BLE001 - an account lookup must never crash the tool; degrade to "none found"
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
            except Exception:  # noqa: BLE001 - skip an instance we cannot read rather than failing the whole lookup
                logging.getLogger("feishu").debug(
                    "could not read instance %s for payment accounts", code, exc_info=True
                )
                continue
            if not _instance_belongs_to(instance, user):
                continue  # defense in depth: never harvest an instance that is not the requesting user's own
            for widget in approval_account_widgets(instance):
                value = widget.get("value")
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
        if user_keys:
            self._cache.setdefault(user_keys[0], {}).update(accounts)
        return list(accounts.values())

    async def resolve(self, user: Mapping[str, Any], account_id: str) -> dict[str, Any] | None:
        r"""把句柄还原为完整账户控件值，严格限定为请求用户本人；缓存未命中时回源一次再试，仍无则返回 `None`。"""
        user_keys = payment_account_keys(user)
        if not user_keys:
            return None
        primary = user_keys[0]
        account = self._cache.get(primary, {}).get(account_id)
        if account is None:
            await self.recent(user)  # repopulate (process restart / first use), then retry
            account = self._cache.get(primary, {}).get(account_id)
        if account is None:
            return None
        if not set(user_keys) & set(account.user_keys):
            return None  # the handle must belong to THIS user
        return dict(account.account_value)
