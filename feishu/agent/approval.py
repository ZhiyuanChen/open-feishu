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
人在环（human-in-the-loop）审批引擎：把「确认即执行」升级为「校验—认领—执行—记账」。

[feishu.agent.approval.ApprovalEngine][] 是 [feishu.agent.loop.Agent][] 审批环节的可插拔策略对象。
默认实现 [feishu.agent.approval.DefaultApprovalEngine][] 在执行被审批工具前依次完成：负载防篡改校验、
幂等重放（同一请求重复确认只执行一次）、并发认领（防止重复执行）、执行后记账与审计，并在执行结果未知时
冻结审批而非放任重试。所有面向用户的措辞均通过 `outcome_status` 注入，SDK 仅保留中性英文兜底，不内置任何
产品文案。

与 [feishu.agent.loop.Agent][] 的对接（集成步骤，非本模块职责）：

- `_request_approval` 改为构造带 `payload_sha256` / `idempotency_key` / 归属信息的
  [feishu.agent.session.PendingApproval][]，调用 `await approval_engine.on_request(approval)`，并由可注入的
  卡片构造器渲染携带 `payload_sha256` 的确认卡片；
- `handle_card_action` 从回传值读取 `payload_sha256`，调用
  `await approval_engine.on_decision(approval_id, decision, expected_payload_sha256=..., dispatch=...)`，
  依据返回的 [feishu.agent.approval.ApprovalOutcome][] 决定回传给模型的工具结果与更新后的卡片。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

from .integrity import derive_idempotency_key
from .result import ToolOutcome, ToolResult
from .session import ClaimResult, PendingApproval, PendingApprovalStore
from .tools import ToolValidationError

Decision = Literal["approve", "reject"]

# Dispatch a tool by (name, arguments) and await its result; the Agent passes
# ToolRegistry.dispatch here so the engine stays decoupled from the registry.
DispatchTool = Callable[[str, dict[str, Any]], Awaitable[ToolResult]]


class ApprovalStatus(str, Enum):
    r"""
    一次审批决策的归一化结果，驱动回传给模型的工具结果与卡片更新。

    由于继承自 `str`，枚举成员可直接与字符串字面量比较。面向用户的具体措辞由
    [feishu.agent.approval.DefaultApprovalEngine][] 的 `outcome_status` 注入，本枚举仅作稳定的机器可读标识。

    Examples:
        >>> ApprovalStatus.EXECUTED == "executed"
        True
    """

    EXECUTED = "executed"  # tool ran; content is its result
    REPLAYED = "replayed"  # idempotent hit; returned a cached prior result without re-running
    REJECTED = "rejected"  # user declined
    TAMPERED = "tampered"  # confirmation payload hash did not match the proposal
    ALREADY_DECIDED = "already_decided"  # a concurrent confirm already claimed/ran it
    SUPERSEDED = "superseded"  # a newer proposal replaced this one
    FROZEN = "frozen"  # execution outcome unknown; frozen to prevent a silent re-run
    EXPIRED = "expired"  # TTL elapsed before confirmation
    MISSING = "missing"  # no such approval
    FAILED = "failed"  # tool ran but reported failure (is_error / non-success outcome); retryable


_DEFAULT_STATUS_TEXT: dict[str, str] = {
    "executed": "Action executed.",
    "replayed": "Action already executed; returning the prior result.",
    "rejected": "User rejected this action.",
    "tampered": "Confirmation did not match the original request; execution refused.",
    "already_decided": "This action was already confirmed or is being executed.",
    "superseded": "This request was superseded by a newer one.",
    "frozen": "Action was submitted but its result is unknown; manual review required.",
    "expired": "This confirmation request has expired.",
    "missing": "No such approval request; it may have expired.",
    "failed": "The action did not succeed and was not recorded as done.",
}


@dataclass(slots=True)
class ApprovalOutcome:
    r"""
    审批决策的结构化结果，告知 [feishu.agent.loop.Agent][] 如何回传模型与更新卡片。

    `content` 是回传给模型的工具结果：`EXECUTED`/`REPLAYED` 时为真实执行结果，其余情形为一段状态说明文本。
    `is_error` 为 `True` 时模型据此调整后续行为；`status` 供产品侧映射卡片样式与展示措辞。

    Examples:
        >>> outcome = ApprovalOutcome(status=ApprovalStatus.EXECUTED, content={"id": "task_1"})
        >>> outcome.status
        <ApprovalStatus.EXECUTED: 'executed'>
        >>> outcome.is_error
        False
    """

    status: ApprovalStatus
    content: Any = None
    is_error: bool = False
    authorize_url: str | None = None


@runtime_checkable
class ExecutionResultStore(Protocol):
    r"""
    幂等执行结果缓存协议：按负载摘要键存取一次成功执行的结果，供重复确认时重放。

    用于实现「已执行的写操作被再次确认时，返回先前结果而非二次提交」。纯机制，不含任何产品语义。
    """

    def get(self, lookup_key: str) -> dict[str, Any] | None:
        r"""按幂等键 / 别名键读取已缓存的执行结果记录，未命中返回 `None`。"""
        ...

    def put(
        self,
        idempotency_key: str,
        *,
        execution_status: str,
        result: Any,
        alias_lookup_keys: tuple[str, ...] = (),
        payload_sha256: str | None = None,
    ) -> None:
        r"""写入一次执行结果，可附带用于去重的别名键与负载摘要。"""
        ...


@runtime_checkable
class AuditLog(Protocol):
    r"""
    仅追加（append-only）审计日志协议：记录审批生命周期事件，供排障与合规复盘。

    事件类型字符串由调用方给出（如 `write_request`/`confirm`/`execute`/`cancel`），存储仅负责落盘。
    """

    def append(
        self,
        event_type: str,
        *,
        key: str,
        approval: PendingApproval | None = None,
        event_id: str | None = None,
        message_id: str | None = None,
        outcome: str = "ok",
        error: str | None = None,
    ) -> None:
        r"""追加一条审计事件。"""
        ...


@runtime_checkable
class ApprovalEngine(Protocol):
    r"""
    审批引擎协议，是 [feishu.agent.loop.Agent][] 人在环环节的可插拔策略契约。

    `on_request` 在工具要求审批时被调用以持久化并准备审批；`on_decision` 在用户于卡片上做出决策后被调用，
    完成校验、执行与记账并返回 [feishu.agent.approval.ApprovalOutcome][]。内置实现为
    [feishu.agent.approval.DefaultApprovalEngine][]。该协议标注了 `runtime_checkable`。

    Examples:
        >>> from feishu.agent.session import InMemoryPendingApprovalStore
        >>> isinstance(DefaultApprovalEngine(approvals=InMemoryPendingApprovalStore()), ApprovalEngine)
        True
    """

    async def on_request(self, approval: PendingApproval) -> None:
        r"""持久化一次挂起审批并完成下发前准备。"""
        ...

    async def on_cancel(self, approval_id: str) -> None:
        r"""撤销一次尚未决策的挂起审批（如确认卡片下发失败），移除记录以免留下用户无法确认的悬挂审批。"""
        ...

    async def on_decision(
        self,
        approval_id: str,
        decision: Decision,
        *,
        expected_payload_sha256: str | None = None,
        dispatch: DispatchTool,
    ) -> ApprovalOutcome:
        r"""依据用户决策完成校验、执行与记账，返回 [feishu.agent.approval.ApprovalOutcome][]。"""
        ...


class DefaultApprovalEngine:
    r"""
    [feishu.agent.approval.ApprovalEngine][] 的参考实现：防篡改 + 幂等重放 + 并发认领 + 冻结未知 + 审计。

    一次 `approve` 决策依次经历：可选的幂等重放命中检查 → 携 `expected_payload_sha256` 的并发认领
    （[feishu.agent.session.PendingApprovalStore.claim][]）→ 经 `dispatch` 执行工具 → 记录执行结果与审计；
    执行抛错时冻结审批（`execution_unknown`）而非放任重试。`reject` 决策直接取消。所有面向用户的措辞均取自
    注入的 `outcome_status`（缺省回退到中性英文），SDK 不内置任何产品文案；`idempotency_namespace` 由产品
    提供以隔离 id 空间。

    Args:
        approvals: 挂起审批存储 [feishu.agent.session.PendingApprovalStore][]。
        executions: 可选的幂等执行结果缓存 [feishu.agent.approval.ExecutionResultStore][]。
        audit: 可选的审计日志 [feishu.agent.approval.AuditLog][]。
        outcome_status: 由 [feishu.agent.approval.ApprovalStatus][] 值到展示措辞的映射，覆盖中性英文兜底。
        idempotency_namespace: 派生幂等键 / id 时的命名空间，用于隔离不同产品的 id 空间。默认为 `"feishu"`。

    Examples:
        >>> from feishu.agent.session import InMemoryPendingApprovalStore
        >>> engine = DefaultApprovalEngine(approvals=InMemoryPendingApprovalStore(), idempotency_namespace="example")
        >>> engine.idempotency_namespace
        'example'
    """

    def __init__(
        self,
        *,
        approvals: PendingApprovalStore,
        executions: ExecutionResultStore | None = None,
        audit: AuditLog | None = None,
        outcome_status: Mapping[str, str] | None = None,
        idempotency_namespace: str = "feishu",
    ) -> None:
        self.approvals = approvals
        self.executions = executions
        self.audit = audit
        self.outcome_status = dict(outcome_status or {})
        self.idempotency_namespace = idempotency_namespace
        self._log = logging.getLogger("feishu")

    def _text(self, status: ApprovalStatus) -> str:
        return self.outcome_status.get(status.value) or _DEFAULT_STATUS_TEXT[status.value]

    async def on_request(self, approval: PendingApproval) -> None:
        r"""持久化挂起审批并写入 `write_request` 审计事件；缺省时按命名空间派生幂等键。"""
        if approval.idempotency_key is None and approval.created_message_id and approval.payload_sha256:
            approval.idempotency_key = derive_idempotency_key(
                message_id=approval.created_message_id,
                payload_sha256=approval.payload_sha256,
                namespace=self.idempotency_namespace,
            )
        await self.approvals.put(approval)
        await self._record("write_request", approval)

    async def on_cancel(self, approval_id: str) -> None:
        r"""
        撤销一次尚未决策的挂起审批：移除记录并写入 `cancel` 审计事件。

        用于确认卡片下发失败等「审批已落库但永远不会被决策」的情形清理，避免留下用户无法确认的悬挂审批。
        无需先 `claim`：调用方场景下卡片从未送达，不存在并发确认与之竞争（与 `on_decision` 的 reject 分支不同）。
        审批不存在时为无操作。
        """
        approval = await self.approvals.get(approval_id)
        await self.approvals.complete(approval_id, outcome="cancelled")
        if approval is not None:
            await self._record("cancel", approval)

    async def on_decision(
        self,
        approval_id: str,
        decision: Decision,
        *,
        expected_payload_sha256: str | None = None,
        dispatch: DispatchTool,
    ) -> ApprovalOutcome:
        r"""
        依据用户决策完成校验、执行与记账。

        Args:
            approval_id: 审批标识。
            decision: 用户决策，`"approve"` 或 `"reject"`。
            expected_payload_sha256: 卡片回传携带的负载摘要，用于防篡改校验。
            dispatch: 工具分发函数，通常为 [feishu.agent.tools.ToolRegistry.dispatch][]。

        Returns:
            结构化的 [feishu.agent.approval.ApprovalOutcome][]。
        """
        approval = await self.approvals.get(approval_id)
        if approval is None:
            return ApprovalOutcome(ApprovalStatus.MISSING, content=self._text(ApprovalStatus.MISSING), is_error=True)

        if decision == "reject":
            # Reject goes through the SAME atomic claim gate as approve: otherwise a concurrent approve+reject
            # on one card could both win (tool executed AND a rejection returned). Only the claim winner proceeds.
            claim = await self.approvals.claim(approval_id, expected_payload_sha256=expected_payload_sha256)
            if claim is not ClaimResult.CLAIMED:
                return self._claim_failure(claim)
            await self.approvals.complete(approval_id, outcome="cancelled")
            await self._record("cancel", approval)
            return ApprovalOutcome(ApprovalStatus.REJECTED, content=self._text(ApprovalStatus.REJECTED), is_error=True)

        # Idempotent replay: a prior execution with a MATCHING payload hash returns its cached
        # result without re-running. A missing/mismatched hash never replays (fail closed).
        if self.executions is not None and approval.idempotency_key and approval.payload_sha256 is not None:
            cached = await asyncio.to_thread(self.executions.get, approval.idempotency_key)
            if cached is not None and cached.get("payload_sha256") == approval.payload_sha256:
                await self.approvals.complete(approval_id, outcome="replayed")
                await self._record("replay", approval)
                return ApprovalOutcome(ApprovalStatus.REPLAYED, content=cached.get("result"))

        claim = await self.approvals.claim(approval_id, expected_payload_sha256=expected_payload_sha256)
        if claim is not ClaimResult.CLAIMED:
            return self._claim_failure(claim)
        await self._record("confirm", approval)

        try:
            result = await dispatch(approval.tool_name, approval.arguments)
        except (KeyError, ToolValidationError) as exc:
            # Raised by dispatch BEFORE the tool body runs (unknown tool / invalid arguments): no side effect
            # happened. Resolve the pending TERMINALLY (the clicked card is patched button-less, so it can't be
            # retried anyway) — the model gets the FAILED result and re-proposes a fresh, confirmable call.
            await self.approvals.complete(approval_id, outcome="failed")
            await self._record("execute_failed", approval, error=str(exc))
            return ApprovalOutcome(ApprovalStatus.FAILED, content=f"{exc}", is_error=True)
        except Exception as exc:  # noqa: BLE001 — the side effect may or may not have landed
            await self.approvals.complete(approval_id, outcome="frozen")
            await self._record("execute_unknown", approval, error=str(exc))
            self._log.exception("approval %s: tool %s raised during execution", approval_id, approval.tool_name)
            return ApprovalOutcome(ApprovalStatus.FROZEN, content=self._text(ApprovalStatus.FROZEN), is_error=True)

        # dispatch() always returns a ToolResult. A tool may signal failure WITHOUT raising
        # (e.g. NEEDS_USER_AUTH, BLOCKED): the write did not happen, so never mark executed or cache it for
        # replay. Resolve TERMINALLY (the clicked card is patched button-less) — the model gets the FAILED
        # result (with any authorize_url) and re-proposes a fresh, confirmable call.
        if result.is_error or result.outcome not in (ToolOutcome.COMPLETED, ToolOutcome.INFORMATIONAL):
            await self.approvals.complete(approval_id, outcome="failed")
            await self._record("execute_failed", approval, error=str(result.outcome.value))
            return ApprovalOutcome(
                ApprovalStatus.FAILED, content=result.content, is_error=True, authorize_url=result.authorize_url
            )

        # Cache and return the UNWRAPPED content so EXECUTED and a later REPLAYED are identical.
        content = result.content
        # Write the idempotent-replay record BEFORE completing the pending. The side effect has already landed;
        # if we completed first and then crashed (or the cache write failed) before recording it, a redelivery
        # would re-execute. So cache first; if the cache write itself fails, FREEZE rather than complete — we
        # can no longer guarantee dedup, so a human reviews instead of risking a duplicate side effect.
        if self.executions is not None and approval.idempotency_key:
            try:
                await asyncio.to_thread(
                    self.executions.put,
                    approval.idempotency_key,
                    execution_status="executed",
                    result=content,
                    payload_sha256=approval.payload_sha256,
                )
            except Exception as exc:  # noqa: BLE001 — executed, but the replay record could not be written
                await self.approvals.complete(approval_id, outcome="frozen")
                await self._record("execute_unknown", approval, error=f"replay-cache write failed: {exc}")
                self._log.exception("approval %s: executed but replay-cache write failed; frozen", approval_id)
                return ApprovalOutcome(ApprovalStatus.FROZEN, content=self._text(ApprovalStatus.FROZEN), is_error=True)
        await self.approvals.complete(approval_id, outcome="executed")
        await self._record("execute", approval)
        return ApprovalOutcome(ApprovalStatus.EXECUTED, content=content)

    def _claim_failure(self, claim: ClaimResult) -> ApprovalOutcome:
        status = {
            ClaimResult.TAMPERED: ApprovalStatus.TAMPERED,
            ClaimResult.ALREADY_CLAIMED: ApprovalStatus.ALREADY_DECIDED,
            ClaimResult.SUPERSEDED: ApprovalStatus.SUPERSEDED,
            ClaimResult.EXPIRED: ApprovalStatus.EXPIRED,
            ClaimResult.MISSING: ApprovalStatus.MISSING,
        }.get(claim, ApprovalStatus.MISSING)
        return ApprovalOutcome(status, content=self._text(status), is_error=True)

    async def _record(self, event_type: str, approval: PendingApproval, *, error: str | None = None) -> None:
        if self.audit is None:
            return
        try:
            await asyncio.to_thread(
                self.audit.append,
                event_type,
                key=approval.approval_id,
                approval=approval,
                event_id=approval.created_event_id,
                message_id=approval.created_message_id,
                outcome="error" if error else "ok",
                error=error,
            )
        except Exception:  # noqa: BLE001 — auditing must never break the decision path
            self._log.exception("approval audit append failed for %s", approval.approval_id)
