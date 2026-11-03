"""Owner-bound scheduled task tools for a configured agent runtime."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ...auth import user_identity_keys
from ...cards.builder import Card
from ..context import current_tool_context
from ..replies import turn_message
from ..scheduled import Schedule, ScheduledTask, ScheduledTaskStore
from ..session import PendingApproval
from ..tools import ToolRegistry

_CREATE = "create_scheduled_task"
_MANAGE = {"pause_scheduled_task": "paused", "resume_scheduled_task": "active", "delete_scheduled_task": "deleted"}


def validate_schedule_request(arguments: Mapping[str, Any], *, now: float) -> tuple[Schedule, str, str]:
    instruction, raw = arguments.get("instruction"), arguments.get("schedule")
    zone, policy = arguments.get("timezone", "UTC"), arguments.get("policy", "read_only")
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 2000:
        raise ValueError("instruction must contain between 1 and 2000 characters")
    if not isinstance(raw, dict) or not isinstance(zone, str) or policy not in ("read_only", "confirm_writes"):
        raise ValueError("invalid schedule, timezone or policy")
    schedule = Schedule.from_dict(raw)
    if schedule.next_after(now, zone) is None:
        raise ValueError("schedule has no future occurrence")
    return schedule, zone, policy


def _requester() -> tuple[str, tuple[str, ...], str]:
    context = current_tool_context()
    event = context.event
    tenant_key = context.resolved_tenant_key()
    owner_keys = user_identity_keys(context.requesting_user())
    chat_id = turn_message(event).get("chat_id") if event is not None else None
    if (
        not isinstance(tenant_key, str)
        or not tenant_key
        or not owner_keys
        or not isinstance(chat_id, str)
        or not chat_id
    ):
        raise PermissionError("a verified tenant, requester and origin chat are required")
    return tenant_key, owner_keys, chat_id


def _info(task: ScheduledTask) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "instruction": task.instruction,
        "schedule": asdict(task.schedule),
        "state": task.state,
        "policy": task.policy,
        "timezone": task.timezone,
        "next_run_at": task.next_run_at,
        "revision": task.revision,
    }


def preflight_scheduled_tool(name: str, arguments: dict[str, Any], store: ScheduledTaskStore, *, now: float) -> None:
    """Validate model-generated mutations before presenting a confirmation card."""
    tenant, owner, _chat = _requester()
    if name == _CREATE:
        validate_schedule_request(arguments, now=now)
        return
    if name in _MANAGE:
        task = store.get(str(arguments.get("task_id") or ""))
        if task is None or task.tenant_key != tenant or not set(task.owner_keys).intersection(owner):
            raise PermissionError("scheduled task is not owned by requester")
        if isinstance(arguments.get("revision"), bool) or arguments.get("revision") != task.revision:
            raise ValueError("scheduled task revision is stale")


def register_scheduled_tools(
    registry: ToolRegistry, store: ScheduledTaskStore, *, clock: Callable[[], float] = time.time
) -> None:
    """Register scheduling tools; the normal Agent approval flow protects mutations."""

    @registry.register(
        _CREATE,
        description="创建需要确认的一次性或周期 Agent 任务。",
        input_schema={
            "type": "object",
            "properties": {
                "instruction": {"type": "string", "maxLength": 2000, "description": "独立执行的完整指令。"},
                "schedule": {
                    "type": "object",
                    "description": "once 用 run_at；interval 用 seconds 和 anchor_at；calendar 用 hour、minute 和可选 weekdays。",
                    "properties": {
                        "kind": {"type": "string", "enum": ["once", "interval", "calendar"]},
                        "run_at": {"type": "number", "description": "单次执行的 UTC Unix 秒。"},
                        "seconds": {"type": "number", "description": "固定间隔秒数，至少 60 秒。"},
                        "anchor_at": {"type": "number", "description": "固定间隔的 UTC Unix 秒锚点。"},
                        "hour": {"type": "integer", "description": "本地时钟小时，0–23。"},
                        "minute": {"type": "integer", "description": "本地时钟分钟，0–59。"},
                        "weekdays": {
                            "type": "array",
                            "items": {"type": "integer", "enum": list(range(7))},
                            "description": "周一为 0，周日为 6；省略或空列表表示每天。",
                        },
                    },
                    "required": ["kind"],
                    "additionalProperties": False,
                },
                "timezone": {"type": "string", "description": "IANA 时区，例如 Asia/Shanghai。"},
                "policy": {
                    "type": "string",
                    "enum": ["read_only", "confirm_writes"],
                    "description": "只读或每次写操作确认；均不允许自动写入。",
                },
            },
            "required": ["instruction", "schedule", "timezone", "policy"],
            "additionalProperties": False,
        },
        requires_approval=True,
        scheduled_effect="write",
    )
    async def create_scheduled_task(
        instruction: str, schedule: dict[str, Any], timezone: str, policy: str
    ) -> dict[str, Any]:
        tenant, owner, chat_id = _requester()
        now = clock()
        parsed, zone, checked_policy = validate_schedule_request(
            {"instruction": instruction, "schedule": schedule, "timezone": timezone, "policy": policy}, now=now
        )
        draft = store.create_draft(
            tenant_key=tenant,
            owner_keys=owner,
            chat_id=chat_id,
            instruction=instruction,
            schedule=parsed,
            now=now,
            timezone=zone,
            policy=checked_policy,
        )
        return _info(store.confirm_draft(draft.draft_id, owner, draft.payload_hash, tenant_key=tenant, now=now))

    @registry.register(
        "list_scheduled_tasks",
        description="列出当前用户的定时任务。",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        scheduled_effect="read",
    )
    async def list_scheduled_tasks() -> list[dict[str, Any]]:
        tenant, owner, _chat = _requester()
        return [_info(task) for task in store.list_owned(owner, tenant_key=tenant)]

    def register_action(name: str, state: str) -> None:
        @registry.register(
            name,
            description="确认后管理当前用户的定时任务。",
            input_schema={
                "type": "object",
                "properties": {"task_id": {"type": "string"}, "revision": {"type": "integer"}},
                "required": ["task_id", "revision"],
                "additionalProperties": False,
            },
            requires_approval=True,
            scheduled_effect="write",
        )
        async def action(task_id: str, revision: int) -> dict[str, Any]:
            tenant, owner, _chat = _requester()
            return _info(
                store.set_state(task_id, owner, state, tenant_key=tenant, expected_revision=revision, now=clock())
            )

    for name, state in _MANAGE.items():
        register_action(name, state)


def _safe(text: Any) -> str:
    # Treat user-supplied prompt as literal card text rather than Markdown.
    value = str(text)
    for char in ("\\", "*", "_", "`", "[", "]", "<", ">"):
        value = value.replace(char, "\\" + char)
    return value


def scheduled_approval_card(approval: PendingApproval, store: ScheduledTaskStore) -> dict[str, Any]:
    """Render the exact operation and arguments bound to an existing approval."""
    args = approval.arguments
    if approval.tool_name == _CREATE:
        schedule, zone, policy = validate_schedule_request(args, now=float(approval.created_at or time.time()))
        next_run = schedule.next_after(float(approval.created_at or time.time()), zone)
        next_text = datetime.fromtimestamp(next_run, ZoneInfo(zone)).isoformat() if next_run is not None else "无"
        detail = (
            f"**任务**：{_safe(args['instruction'])}\n\n**计划**：{_safe(json.dumps(args['schedule'], ensure_ascii=False))}"
            f"\n\n**下次执行**：{_safe(next_text)}\n\n**策略**：{_safe(policy)}"
        )
    elif approval.tool_name in _MANAGE:
        task = store.get(str(args.get("task_id") or ""))
        if (
            task is None
            or task.tenant_key != approval.tenant_key
            or not set(task.owner_keys).intersection(approval.owner_user_keys)
        ):
            raise PermissionError("scheduled task is not owned by requester")
        detail = f"**任务**：{_safe(task.instruction)}\n\n**操作**：{_safe(approval.tool_name)}"
    else:
        raise ValueError("not a scheduled task approval")
    detail += f"\n\n**创建者**：{_safe(', '.join(approval.owner_user_keys))}\n\n**会话**：{_safe(approval.chat_id)}"
    payload = {"__approval__": approval.approval_id, "payload_sha256": approval.payload_sha256}
    return (
        Card()
        .header("确认定时任务", template="orange")
        .markdown(detail)
        .button("确认", value={**payload, "decision": "approve"}, type="primary")
        .button("取消", value={**payload, "decision": "reject"}, type="danger")
        .to_dict()
    )
