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

r"""审批工具工厂：列出审批定义、读取审批定义表单、创建审批实例（需审批）。详见 [feishu.agent.toolkit][]。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ...approval import (
    approval_account_label,
    approval_account_number,
    approval_definition_index,
    approval_definition_schema,
    approval_definition_summary,
    approval_form_payloads,
    approval_form_problems,
    approval_instance,
    approval_instance_participant_ids,
)
from ...approval.files import approval_file_code
from ..context import current_tool_context
from ..result import ToolOutcome, ToolResult
from ..tools import Tool
from ._base import (
    list_recent_payment_accounts,
    needs_user_auth,
    resolve_client,
    resolve_payment_account,
    resolve_shared_file_bytes,
)


def _require_identity(user: Mapping[str, Any]) -> ToolResult | None:
    r"""
    Fail-closed 身份闸门：无法识别请求用户（无可写入审批接口的 id）时拒绝这类必须以本人身份写入的审批操作。

    审批实例 / 任务接口需携带申请人 / 审批人 id；身份缺失时与其把 `None` 发给接口（产生越权或不可预期行为），
    不如就地拒绝并让模型据此向用户说明。返回 `None` 表示身份可用、可继续。
    """
    if _requester_id(user) is None:
        return ToolResult(
            ToolOutcome.BLOCKED,
            content="cannot perform this action: the requesting user could not be identified",
            is_error=True,
        )
    return None


def _requester_id(user: Mapping[str, Any]) -> tuple[str, str] | None:
    for kind in ("open_id", "user_id"):
        value = user.get(kind)
        if value:
            return str(value), kind
    return None


def _form_to_mapping(form: Any) -> dict[str, Any]:
    r"""把审批表单（mapping / 序列化 JSON / 控件载荷列表）归一为 `widget_id -> value` 映射，便于并入文件控件。"""
    if form is None:
        return {}
    if isinstance(form, str):
        try:
            form = json.loads(form)
        except (TypeError, ValueError):
            return {}
    if isinstance(form, Mapping):
        return dict(form)
    if isinstance(form, list):
        return {item["id"]: item["value"] for item in form if isinstance(item, Mapping) and "id" in item}
    return {}


def _account_fields_in_form(index: Mapping[str, Mapping[str, Any]], values: Mapping[str, Any]) -> list[str]:
    r"""返回直接出现在 `form` 参数中的 account 控件问题，避免模型把原始账户对象混入表单。"""
    problems: list[str] = []
    for widget_id, entry in index.items():
        if entry.get("is_child") or (entry.get("type") or "").lower() != "account":
            continue
        if widget_id in values:
            name = entry.get("name") or widget_id
            problems.append(
                f"field '{name}' (account) must be selected via the accounts parameter using "
                "list_my_payment_accounts; raw account values in form are not accepted"
            )
    return problems


def _redact_account_values(value: Any) -> Any:
    r"""递归脱敏审批实例中的账户对象，确保模型只看到 label，不看到完整银行卡号。"""
    if isinstance(value, Mapping):
        if approval_account_number(value):
            return {"label": approval_account_label(value), "redacted": True}
        return {key: _redact_account_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_account_values(item) for item in value]
    return value


def _approval_instance_for_model(instance: Mapping[str, Any]) -> dict[str, Any]:
    r"""返回可回传给模型的审批实例视图；其中账户表单值已脱敏。"""
    data = _redact_account_values(instance)
    if not isinstance(data, dict):
        return {}
    form = instance.get("form")
    if isinstance(form, str):
        try:
            data["form"] = _redact_account_values(json.loads(form))
        except (TypeError, ValueError):
            data["form"] = "[redacted: could not parse approval form]"
    return data


def list_approval_definitions(
    *,
    description: str,
    name: str = "list_approval_definitions",
    locale: str = "zh-CN",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：列出当前身份可发起的审批定义，返回一个 [feishu.agent.tools.Tool][]。

    飞书按调用身份过滤可发起的审批定义，因此默认以请求用户身份（`as_user=True`）读取。
    每项摘要通常包含 `approval_code` 与 `approval_name`，供后续 `get_approval_definition`
    与 `create_approval_instance` 使用。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"list_approval_definitions"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        as_user: 是否以请求用户身份读取。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = list_approval_definitions(description="列出审批定义")
        >>> tool.name, tool.requires_approval
        ('list_approval_definitions', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "max_items": {"type": "integer", "description": "Maximum number of definitions to return"},
            "locale": {"type": "string", "description": "Localization tag, e.g. zh-CN or en-US"},
        },
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        items = await client.approval.definitions.list(
            locale=arguments.get("locale") or locale,
            max_items=arguments.get("max_items"),
        )
        definitions = [approval_definition_summary(item) for item in items]
        return ToolResult(ToolOutcome.COMPLETED, content=definitions)

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)


def get_approval_definition(
    *,
    description: str,
    name: str = "get_approval_definition",
    locale: str = "zh-CN",
    as_user: bool = False,  # the get-definition endpoint requires a TENANT token (user token → 99991668)
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：按 `approval_code` 读取单个审批定义的表单结构，返回一个 [feishu.agent.tools.Tool][]。

    返回经 [feishu.approval.approval_definition_schema][] 归一化后的紧凑表单 schema（含 `fields`
    控件列表），供模型据此构造 `create_approval_instance` 的 `form`。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"get_approval_definition"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        as_user: 是否以请求用户身份读取。默认为 `False`——「查看审批定义」接口仅接受租户令牌（用户令牌会返回
            99991668）；审批定义为组织级配置而非用户私有数据，以租户身份读取符合最小权限。注意「列出审批定义」
            （[feishu.agent.toolkit.approvals.list_approval_definitions][]）恰好相反，仅接受用户令牌。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = get_approval_definition(description="读取审批定义")
        >>> tool.name, tool.requires_approval
        ('get_approval_definition', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "approval_code": {"type": "string", "description": "Approval definition code"},
            "locale": {"type": "string", "description": "Localization tag, e.g. zh-CN or en-US"},
        },
        "required": ["approval_code"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        definition = await client.approval.definitions.get(
            arguments["approval_code"],
            locale=arguments.get("locale") or locale,
        )
        return ToolResult(ToolOutcome.COMPLETED, content=approval_definition_schema(definition))

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)


def create_approval_instance(
    *,
    description: str,
    name: str = "create_approval_instance",
    locale: str = "zh-CN",
    requires_approval: bool = True,
    as_user: bool = False,  # the create-instance endpoint is tenant-token-only; applicant is forced below
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：基于 `approval_code` 与模型构造的 `form` 创建审批实例，返回一个需审批的 [feishu.agent.tools.Tool][]。

    `requires_approval=True` 时，[feishu.agent.loop.Agent][] 先挂起并发审批卡片；用户批准后处理函数才执行写入。
    申请人身份**强制**为当前轮的请求用户（[feishu.agent.context.ToolContext.requesting_user][]），模型无法
    覆盖，以防越权代他人提交。`form` 接受 `widget_id -> value` 映射、完整控件载荷列表或已序列化的 JSON 串，
    由 [feishu.approval.approval_instance][] 归一化为 Feishu 审批实例载荷。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"create_approval_instance"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        requires_approval: 是否需用户二次确认。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `False`——创建审批实例接口仅接受租户令牌（用户令牌会返回
            99991668）；申请人身份在处理函数中强制为请求用户本人，零信任不受影响。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的需审批 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = create_approval_instance(description="创建审批")
        >>> tool.name, tool.requires_approval
        ('create_approval_instance', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "approval_code": {"type": "string", "description": "Approval definition code"},
            "form": {
                "description": (
                    "Approval form as a widget_id -> value mapping (or serialized JSON) built from the "
                    "definition schema. Fill EVERY required widget: plain text for input/textarea, the "
                    "option id for radioV2, a number for amount/formula (formula = the computed total, e.g. "
                    "the sum of the amount rows), YYYY-MM-DD for date, and a list of row objects for a "
                    "fieldList (费用明细). For account/收款账户 widgets, pass {widget_id: account_id} in "
                    "'accounts' using account_id from list_my_payment_accounts; never put raw account values "
                    "or handles in 'form'. The handler types and serializes each value from the definition."
                ),
                "type": ["object", "array", "string"],
            },
            "department_id": {"type": "string", "description": "Applicant department_id (optional)"},
            "attachments": {
                "type": "object",
                "description": (
                    "Optional file widgets: {widget_id: shared file_id, or [file_id, ...]}. Each shared file_id "
                    "is resolved and uploaded to the approval, and its returned code is placed in that widget."
                ),
                "additionalProperties": {"type": ["string", "array"]},
            },
            "accounts": {
                "type": "object",
                "description": (
                    "Optional payment-account (收款账户) widgets: {widget_id: account_id}. account_id comes "
                    "from list_my_payment_accounts and is resolved to the requesting user's own account value."
                ),
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["approval_code", "form"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        # Least-privilege: the applicant is ALWAYS the requesting user — never model-overridable
        # (prevents a jailbroken agent from submitting an approval as someone else).
        user = current_tool_context().requesting_user()
        blocked = _require_identity(user)
        if blocked is not None:
            return blocked
        approval_code = arguments["approval_code"]
        # Fetch the definition (tenant) to drive TYPED serialization + required-coverage validation: the form
        # value contract is per-widget-type (amount carries a sibling currency, date is RFC3339, formula is a
        # number, fieldList is a 2D array, attachmentV2 is a code list), and Feishu only reports an opaque
        # 1390001 on any mismatch or missing required field.
        definition = await client.approval.definitions.get(approval_code, locale=locale)
        index = approval_definition_index(definition)
        form_mapping = _form_to_mapping(arguments["form"])
        form_account_problems = _account_fields_in_form(index, form_mapping)
        if form_account_problems:
            return ToolResult(
                ToolOutcome.FAILED,
                content="cannot submit this approval form:\n- " + "\n- ".join(form_account_problems),
                is_error=True,
            )
        attachments = arguments.get("attachments") or {}
        for widget_id, file_ids in attachments.items():
            # Resolve each shared file_id (per requesting user), upload it to the approval, and place the
            # returned code(s) into the named file/image widget. The model passes file_ids, never bytes.
            ids = file_ids if isinstance(file_ids, list) else [file_ids]
            codes: list[str] = []
            for shared_file_id in ids:
                resolved = await resolve_shared_file_bytes(str(shared_file_id))
                if isinstance(resolved, ToolResult):
                    return resolved
                data, shared_file = resolved
                upload = await client.approval.files.upload(
                    data,
                    file_name=shared_file.name,
                    file_type="image" if shared_file.kind == "image" else "attachment",
                    media_type=shared_file.media_type,
                )
                code = approval_file_code(upload)
                if not code:
                    return ToolResult(
                        ToolOutcome.FAILED,
                        content=f"failed to upload shared file {shared_file_id} to the approval",
                        is_error=True,
                    )
                codes.append(code)
            form_mapping[str(widget_id)] = codes
        accounts = arguments.get("accounts") or {}
        resolved_account_widget_ids: set[str] = set()
        for widget_id, account_id in accounts.items():
            # Resolve each handle to the requesting user's OWN account value; the model never sees raw bank data.
            value = await resolve_payment_account(str(account_id))
            if isinstance(value, ToolResult):
                return value
            widget_key = str(widget_id)
            form_mapping[widget_key] = value
            resolved_account_widget_ids.add(widget_key)
        # Validate required coverage BEFORE submitting — a precise reason (which field is missing, or that a
        # payment-account control is not fillable via the open API) beats Feishu's opaque 1390001 and lets
        # the agent ask the user / hand off to the Feishu UI instead of silently failing.
        problems = approval_form_problems(
            index,
            form_mapping,
            resolved_account_widget_ids=resolved_account_widget_ids,
        )
        if problems:
            return ToolResult(
                ToolOutcome.FAILED,
                content="cannot submit this approval form:\n- " + "\n- ".join(problems),
                is_error=True,
            )
        # Definition-aware serialization to Feishu's typed widget-payload list ({id, type, value[, currency]},
        # 2D fieldList groups, RFC3339 dates) — passed to approval_instance, which JSON-stringifies it.
        payloads = approval_form_payloads(index, form_mapping)
        api_payload = approval_instance(
            approval_code,
            form=payloads,
            user_id=user.get("user_id"),
            open_id=user.get("open_id"),
            department_id=arguments.get("department_id"),
        )
        result = await client.approval.instances.create(api_payload)
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def cancel_approval_instance(
    *,
    description: str,
    name: str = "cancel_approval_instance",
    locale: str = "zh-CN",
    requires_approval: bool = True,
    as_user: bool = False,  # approval instance/task API is tenant-token-only; canceller forced below
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：撤回一个审批实例，返回一个需审批的 [feishu.agent.tools.Tool][]。

    `requires_approval=True` 时，[feishu.agent.loop.Agent][] 先挂起并发审批卡片；用户批准后处理函数才执行写入，
    调用 `client.approval.instances.cancel(approval_code, instance_code, user_id, user_id_type=...)`。撤回人身份
    **强制**为当前轮的请求用户（[feishu.agent.context.ToolContext.requesting_user][]），模型无法覆盖——
    `instance_code` 标识的审批实例必须由该用户本人发起，以防越权代他人撤回。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"cancel_approval_instance"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        requires_approval: 是否需用户二次确认。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `False`——审批实例 / 任务接口仅接受租户令牌（用户令牌会返回
            99991668）；操作主体仍强制为请求用户本人，零信任不受影响。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的需审批 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = cancel_approval_instance(description="撤回审批实例")
        >>> tool.name, tool.requires_approval
        ('cancel_approval_instance', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "approval_code": {"type": "string", "description": "Approval definition code"},
            "instance_code": {"type": "string", "description": "Approval instance code to cancel"},
        },
        "required": ["approval_code", "instance_code"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        # Least-privilege: the canceller is ALWAYS the requesting user — never model-overridable
        # (prevents a jailbroken agent from cancelling an instance on someone else's behalf).
        user = current_tool_context().requesting_user()
        blocked = _require_identity(user)
        if blocked is not None:
            return blocked
        requester = _requester_id(user)
        if requester is None:
            return ToolResult(
                ToolOutcome.BLOCKED,
                content="cannot perform this action: the requesting user could not be identified",
                is_error=True,
            )
        requester_id, user_id_type = requester
        result = await client.approval.instances.cancel(
            arguments["approval_code"],
            arguments["instance_code"],
            requester_id,
            user_id_type=user_id_type,
        )
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def get_approval_status(
    *,
    description: str,
    name: str = "get_approval_status",
    locale: str = "zh-CN",
    as_user: bool = False,  # GET approval instance is tenant-token-only (user token → 99991668)
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：按 `instance_code` 读取单个审批实例详情，返回一个 [feishu.agent.tools.Tool][]。

    调用 `client.approval.instances.get(instance_id)`，返回的实例含 `approval_code`、`approval_name`、
    `status`、`form`、`task_list`、`comment_list`、`timeline` 等字段，供模型据此向用户概述审批进展，并从
    `task_list` 中识别可供同意 / 拒绝的 `task_id`。`instance_code` 与飞书的 `instance_id` 同义，二者均可。

    最小权限（zero-trust）：实例读取走租户令牌，可按 id 取到**任意**实例（含收款账户等表单数据），故此处
    强制校验「请求用户必须是该实例的参与者」（发起人，或 `task_list`/`timeline` 中的审批人 / 处理人，见
    [feishu.approval.approval_instance_participant_ids][]）；否则返回 `BLOCKED`——防止被越权操控的智能体凭
    实例 id 拉取他人审批与银行账户信息。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"get_approval_status"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        as_user: 是否以请求用户身份读取。默认为 `False`——查询审批实例接口仅接受租户令牌（用户令牌会返回
            99991668）；返回前按参与者校验，仅发起人 / 审批人本人可见，零信任不受影响。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = get_approval_status(description="查询审批实例状态")
        >>> tool.name, tool.requires_approval
        ('get_approval_status', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "instance_code": {
                "type": "string",
                "description": "Approval instance code (a.k.a. instance_id) to fetch",
            },
        },
        "required": ["instance_code"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        # Least-privilege: instance reads run on a TENANT token (user token → 99991668), which means this
        # endpoint can fetch ANY instance — including its bank/account form data — by id. Fail closed unless
        # the requesting user is a participant (applicant or an approver/actor on the instance), so a
        # jailbroken agent cannot harvest other people's approvals.
        user = current_tool_context().requesting_user()
        requester_ids = {value for value in (user.get("open_id"), user.get("user_id"), user.get("union_id")) if value}
        if not requester_ids:
            return ToolResult(
                ToolOutcome.BLOCKED, content="cannot resolve the requesting user's identity", is_error=True
            )
        instance = await client.approval.instances.get(arguments["instance_code"])
        if not (requester_ids & approval_instance_participant_ids(instance)):
            return ToolResult(
                ToolOutcome.BLOCKED,
                content="this approval is not yours to view (you are neither its applicant nor an approver)",
                is_error=True,
            )
        return ToolResult(ToolOutcome.COMPLETED, content=_approval_instance_for_model(instance))

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)


def approve_approval_task(
    *,
    description: str,
    name: str = "approve_approval_task",
    locale: str = "zh-CN",
    requires_approval: bool = True,
    as_user: bool = False,  # tasks/approve is tenant-token-only; approver forced to requesting user below
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：同意一个审批任务，返回一个需审批的 [feishu.agent.tools.Tool][]。

    `requires_approval=True` 时，[feishu.agent.loop.Agent][] 先挂起并发审批卡片；用户批准后处理函数才执行写入，
    调用 `client.approval.tasks.approve(task)`。审批人身份**强制**为当前轮的请求用户
    （[feishu.agent.context.ToolContext.requesting_user][]），模型无法覆盖——`task_id` 标识的待办必须属于该用户本人，
    以防越权代他人审批。`comment` 为可选审批意见。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"approve_approval_task"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        requires_approval: 是否需用户二次确认。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `False`——审批实例 / 任务接口仅接受租户令牌（用户令牌会返回
            99991668）；操作主体仍强制为请求用户本人，零信任不受影响。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的需审批 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = approve_approval_task(description="同意审批任务")
        >>> tool.name, tool.requires_approval
        ('approve_approval_task', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "approval_code": {"type": "string", "description": "Approval definition code"},
            "instance_code": {"type": "string", "description": "Approval instance code the task belongs to"},
            "task_id": {"type": "string", "description": "Approval task id to approve"},
            "comment": {"type": "string", "description": "Optional approval comment"},
        },
        "required": ["approval_code", "instance_code", "task_id"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        # Least-privilege: the approver is ALWAYS the requesting user — never model-overridable
        # (prevents a jailbroken agent from approving a task on someone else's behalf).
        user = current_tool_context().requesting_user()
        blocked = _require_identity(user)
        if blocked is not None:
            return blocked
        task: dict[str, Any] = {
            "approval_code": arguments["approval_code"],
            "instance_code": arguments["instance_code"],
            "task_id": arguments["task_id"],
        }
        if user.get("user_id"):
            task["user_id"] = user["user_id"]
        if user.get("open_id"):
            task["open_id"] = user["open_id"]
        if arguments.get("comment"):
            task["comment"] = arguments["comment"]
        result = await client.approval.tasks.approve(task)
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def reject_approval_task(
    *,
    description: str,
    name: str = "reject_approval_task",
    locale: str = "zh-CN",
    requires_approval: bool = True,
    as_user: bool = False,  # tasks/reject is tenant-token-only; rejecter forced to requesting user below
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：拒绝一个审批任务，返回一个需审批的 [feishu.agent.tools.Tool][]。

    `requires_approval=True` 时，[feishu.agent.loop.Agent][] 先挂起并发审批卡片；用户批准后处理函数才执行写入，
    调用 `client.approval.tasks.reject(task)`。审批人身份**强制**为当前轮的请求用户
    （[feishu.agent.context.ToolContext.requesting_user][]），模型无法覆盖——`task_id` 标识的待办必须属于该用户本人，
    以防越权代他人审批。`comment` 为可选审批意见。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"reject_approval_task"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        requires_approval: 是否需用户二次确认。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `False`——审批实例 / 任务接口仅接受租户令牌（用户令牌会返回
            99991668）；操作主体仍强制为请求用户本人，零信任不受影响。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的需审批 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = reject_approval_task(description="拒绝审批任务")
        >>> tool.name, tool.requires_approval
        ('reject_approval_task', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "approval_code": {"type": "string", "description": "Approval definition code"},
            "instance_code": {"type": "string", "description": "Approval instance code the task belongs to"},
            "task_id": {"type": "string", "description": "Approval task id to reject"},
            "comment": {"type": "string", "description": "Optional rejection comment"},
        },
        "required": ["approval_code", "instance_code", "task_id"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        # Least-privilege: the approver is ALWAYS the requesting user — never model-overridable
        # (prevents a jailbroken agent from rejecting a task on someone else's behalf).
        user = current_tool_context().requesting_user()
        blocked = _require_identity(user)
        if blocked is not None:
            return blocked
        task: dict[str, Any] = {
            "approval_code": arguments["approval_code"],
            "instance_code": arguments["instance_code"],
            "task_id": arguments["task_id"],
        }
        if user.get("user_id"):
            task["user_id"] = user["user_id"]
        if user.get("open_id"):
            task["open_id"] = user["open_id"]
        if arguments.get("comment"):
            task["comment"] = arguments["comment"]
        result = await client.approval.tasks.reject(task)
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def list_my_pending_approvals(
    *,
    description: str,
    name: str = "list_my_pending_approvals",
    locale: str = "zh-CN",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：列出「请求用户本人」的待办审批任务，返回一个 [feishu.agent.tools.Tool][]。

    最小权限（zero-trust）：仅查询发起请求的用户本人的待办（`topic="1"`），用户身份取自
    [feishu.agent.context.ToolContext.requesting_user][]，模型无法指向他人。每个任务含 `instance_code`
    与 `task_id`，可配合 [feishu.agent.toolkit.approvals.approve_approval_task][] /
    [feishu.agent.toolkit.approvals.reject_approval_task][] 处理。

    Examples:
        >>> tool = list_my_pending_approvals(description="列出我的待审批")
        >>> tool.name, tool.requires_approval
        ('list_my_pending_approvals', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"max_items": {"type": "integer", "description": "max tasks to return"}},
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        # Least-privilege: only the requesting user's own pending tasks.
        user_id = current_tool_context().requesting_user().get("open_id")
        if not user_id:
            return ToolResult(
                ToolOutcome.BLOCKED, content="cannot resolve the requesting user's identity", is_error=True
            )
        tasks = await client.approval.tasks.query(
            user_id, topic="1", user_id_type="open_id", max_items=int(arguments.get("max_items") or 20)
        )
        return ToolResult(ToolOutcome.COMPLETED, content=tasks)

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)


def list_my_payment_accounts(
    *,
    description: str,
    name: str = "list_my_payment_accounts",
    locale: str = "zh-CN",
) -> Tool:
    r"""
    读类工厂：列出请求用户本人的收款账户（脱敏标签 + 不可逆句柄），返回一个 [feishu.agent.tools.Tool][]。

    飞书没有公开的“枚举绑定收款账户”接口；这里从用户本人历史审批实例中恢复可复用账户值，只返回
    `account_id`（句柄）与 `label`（脱敏，如“杭州银行 ****8383 (张三)”）。选定后把
    `{widget_id: account_id}` 传入 [feishu.agent.toolkit.approvals.create_approval_instance][] 的 `accounts`
    参数。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"list_my_payment_accounts"`。
        locale: 本地化标识。默认为 `"zh-CN"`。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = list_my_payment_accounts(description="列出我的收款账户")
        >>> tool.name, tool.requires_approval
        ('list_my_payment_accounts', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "approval_code": {
                "type": "string",
                "description": "Optional: limit to accounts used in this approval type",
            },
            "limit": {"type": "integer", "description": "max accounts to list; defaults to 10"},
        },
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        accounts = await list_recent_payment_accounts(
            approval_code=arguments.get("approval_code"), limit=int(arguments.get("limit") or 10)
        )
        return ToolResult(ToolOutcome.COMPLETED, content=[account.summary() for account in accounts])

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)
