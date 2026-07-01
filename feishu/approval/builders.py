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

import json
from collections.abc import Iterable, Mapping
from typing import Any

from chanfig import NestedDict


def approval_form_field(
    widget_id: str,
    value: Any,
    *,
    widget_type: str | None = None,
    name: str | None = None,
) -> NestedDict:
    r"""
    构造一个飞书审批表单控件值。

    Args:
        widget_id: 审批定义中的表单控件 ID。
        value: 该控件接受的取值。
        widget_type: 可选飞书控件类型。
        name: 可选可读字段名。

    Returns:
        单个表单控件载荷，可交给 [feishu.approval.approval_form][] 序列化。

    Examples:
        >>> approval_form_field("amount", "12.30", widget_type="amount").id
        'amount'
    """
    field = NestedDict(id=widget_id, value=value)
    if widget_type:
        field.type = widget_type
    if name:
        field.name = name
    return field


def approval_form(fields: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> str:
    r"""
    序列化飞书审批表单字段。

    Args:
        fields: `widget_id -> value` 映射，或完整控件载荷的可迭代对象。

    Returns:
        可用于 `approval/v4/instances` 的 `form` 字段的 JSON 字符串。

    Examples:
        >>> approval_form({"amount": "12.30"})
        '[{"id": "amount", "value": "12.30"}]'
    """
    if isinstance(fields, Mapping):
        items = [approval_form_field(str(key), value) for key, value in fields.items()]
    else:
        items = [item if isinstance(item, NestedDict) else NestedDict(item) for item in fields]
    return json.dumps(_jsonable(items), ensure_ascii=False)


def approval_instance(
    approval_code: str,
    *,
    form: str | Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
    user_id: str | None = None,
    open_id: str | None = None,
    department_id: str | None = None,
    node_approver_user_id_list: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> NestedDict:
    r"""
    构造飞书审批实例创建载荷。

    Args:
        approval_code: 审批定义 Code。
        form: 已序列化表单 JSON、`widget_id -> value` 映射，或完整控件载荷。
        user_id: 申请人的用户 ID。
        open_id: 申请人的 Open ID。
        department_id: 申请人所属部门 ID。
        node_approver_user_id_list: 可选自选审批人列表。
        extra: 其他需要透传的飞书字段。

    Returns:
        可传给 [feishu.approval.instances.InstancesNamespace.create][] 的审批实例载荷。
    """
    instance = NestedDict(approval_code=approval_code)
    if form is not None:
        instance.form = form if isinstance(form, str) else approval_form(form)
    if user_id:
        instance.user_id = user_id
    if open_id:
        instance.open_id = open_id
    if department_id:
        instance.department_id = department_id
    if node_approver_user_id_list:
        instance.node_approver_user_id_list = node_approver_user_id_list
    for key, value in extra.items():
        if value is not None:
            instance[key] = value
    return instance


# Widget types that Feishu's open approval API documents as unsupported for direct instance creation. They cannot be
# filled via `approval/v4/instances` and must be handled in the Feishu approval admin backend / UI.
# NOTE: `account` (payment account) is deliberately NOT here. Feishu documents it as unsupported for direct input, but
# historical instances expose a self-contained account object that can be resubmitted for the same user via
# [feishu.agent.payment_accounts][]. Raw model-provided account strings are still rejected by
# [feishu.approval.approval_form_problems][].
APPROVAL_API_UNSUPPORTED_WIDGET_TYPES: frozenset[str] = frozenset(
    {
        "text",
        "mutableGroup",
        "serialNumber",
        "tripGroup",
        "apaascorehrOnboardingGroup",
        "apaascorehrRegularateGroup",
        "remedyGroupV2",
        "apaascorehrJobAdjustGroup",
        "apaascorehrOffboardingGroup",
    }
)

# Widget types whose submitted value must be a list (wrap a single value), per the create-instance contract.
_APPROVAL_LIST_VALUE_TYPES: frozenset[str] = frozenset(
    {"checkbox", "checkboxv2", "multiselect", "attachment", "attachmentv2", "image", "imagev2", "contact", "connect"}
)


def approval_definition_index(definition: Mapping[str, Any]) -> dict[str, NestedDict]:
    r"""
    将审批定义表单索引为 ``widget_id -> {type, name, required, is_child, children}``。

    与会拍平结构的 [feishu.approval.approval_definition_widgets][] 不同，本函数保留 `fieldList`
    （费用明细等可重复分组）控件的父子关系：父控件条目携带有序 `children`，子控件条目标记
    `is_child=True`。该索引用于定义感知序列化 [feishu.approval.approval_form_payloads][]，
    以及必填覆盖校验 [feishu.approval.approval_form_problems][]。

    Examples:
        >>> import json
        >>> form = json.dumps([
        ...     {"id": "w1", "type": "input", "name": "标题", "required": True},
        ...     {"id": "wl", "type": "fieldList", "name": "明细", "required": True,
        ...      "children": [{"id": "wa", "type": "amount", "name": "金额", "required": True}]},
        ... ])
        >>> idx = approval_definition_index({"form": form})
        >>> idx["wl"]["children"], idx["wa"]["is_child"]
        (['wa'], True)
    """
    index: dict[str, NestedDict] = {}

    def visit(node: Any, *, is_child: bool) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, is_child=is_child)
            return
        if not isinstance(node, Mapping):
            return
        data = NestedDict(node)
        widget_id = _first_string(data, "id", "widget_id", "widgetId", "custom_id", "customId")
        children = node.get("children")
        if widget_id:
            entry = NestedDict(
                type=_first_string(data, "type", "widget_type", "widgetType"),
                name=_localized_name(data),
                required=bool(node.get("required")),
                is_child=is_child,
            )
            if isinstance(children, list):
                child_ids = [cid for c in children if isinstance(c, Mapping) and (cid := _child_id(c))]
                if child_ids:
                    entry.children = child_ids
            index[widget_id] = entry
        if isinstance(children, list):
            for child in children:
                visit(child, is_child=True)

    visit(_loads_json(definition.get("form")), is_child=False)
    return index


def approval_form_payloads(
    index: Mapping[str, Mapping[str, Any]],
    values: Mapping[str, Any],
    *,
    default_currency: str = "CNY",
    tz_offset: str = "+08:00",
) -> list[dict[str, Any]]:
    r"""
    按审批定义把 ``widget_id -> value`` 序列化为创建实例所需的表单控件载荷。

    每个元素会成为飞书文档约定的 ``{"id", "type", "value"}`` 结构，并按控件类型格式化：
    `amount` 生成数字 `value` 和同级 `currency`；`date` 转为带 `tz_offset` 的 RFC3339；
    `formula` / `number` 转为 JSON 数字；列表值控件（如 attachmentV2 / checkboxV2 / contact / connect）
    转为数组；`fieldList` 转为二维的类型化子控件行。未知类型保持原值透传。

    Args:
        index: [feishu.approval.approval_definition_index][] 的输出（控件 ID 到条目的映射）。
        values: 调用方提供的 ``widget_id -> value`` 映射（模型表单值 + 已合并的附件 code）。
        default_currency: `amount` 控件未携带货币时使用的默认货币，默认 `"CNY"`。
        tz_offset: 裸 `YYYY-MM-DD` 日期补齐时间时附加的时区偏移，默认 `"+08:00"`。

    Examples:
        >>> import json
        >>> form = json.dumps([
        ...     {"id": "wa", "type": "amount", "required": True},
        ...     {"id": "wd", "type": "date", "required": True},
        ... ])
        >>> idx = approval_definition_index({"form": form})
        >>> approval_form_payloads(idx, {"wa": {"currency": "CNY", "value": 153.52}})
        [{'id': 'wa', 'type': 'amount', 'value': 153.52, 'currency': 'CNY'}]
        >>> approval_form_payloads(idx, {"wd": "2026-06-12"})
        [{'id': 'wd', 'type': 'date', 'value': '2026-06-12T00:00:00+08:00'}]
    """
    payloads: list[dict[str, Any]] = []
    for widget_id, value in values.items():
        entry = index.get(widget_id) or {}
        widget_type = entry.get("type")
        formatted, extra = _format_approval_value(
            index, widget_type, value, default_currency=default_currency, tz_offset=tz_offset
        )
        item: dict[str, Any] = {"id": widget_id}
        if widget_type:
            item["type"] = widget_type
        item["value"] = formatted
        item.update(extra)
        payloads.append(item)
    return payloads


def approval_form_problems(
    index: Mapping[str, Mapping[str, Any]],
    values: Mapping[str, Any],
    *,
    resolved_account_widget_ids: Iterable[str] = (),
) -> list[str]:
    r"""
    在触发飞书不透明的 ``1390001`` 前，返回表单会校验失败的人类可读原因。

    本函数检查每个顶层必填控件：API 不支持的类型（见
    [feishu.approval.APPROVAL_API_UNSUPPORTED_WIDGET_TYPES][]）会报告为不可填写；缺失值会按字段名报告；
    `fieldList` 会校验至少有一行，且每行都包含其必填子控件。`account` 控件默认不接受 `form`
    里的任何值；只有调用方通过 `resolved_account_widget_ids` 明确标记该字段来自受信任账户解析器时，
    才接受已解析出的账户对象。返回空列表表示表单看起来完整。

    Examples:
        >>> import json
        >>> form = json.dumps([
        ...     {"id": "wr", "type": "textarea", "name": "事由", "required": True},
        ...     {"id": "wt", "type": "text", "name": "流水号", "required": True},
        ... ])
        >>> idx = approval_definition_index({"form": form})
        >>> approval_form_problems(idx, {"wr": "团建"})
        ["required field '流水号' (text) cannot be filled via the open API; please add it in Feishu and submit there"]
    """
    problems: list[str] = []
    provided = set(values)
    resolved_accounts = {str(widget_id) for widget_id in resolved_account_widget_ids}
    for widget_id, entry in index.items():
        if entry.get("is_child"):
            continue
        widget_type = entry.get("type") or ""
        name = entry.get("name") or widget_id
        if widget_type.lower() == "account" and widget_id in provided:
            if widget_id not in resolved_accounts:
                problems.append(
                    f"field '{name}' (account) must be supplied by a trusted payment-account resolver; "
                    "raw account values in form are not accepted"
                )
                continue
            if not _is_account_widget_value(values.get(widget_id)):
                problems.append(
                    f"field '{name}' (account) resolved account value is invalid; refresh the trusted account value"
                )
                continue
        if not entry.get("required"):
            continue
        if widget_type in APPROVAL_API_UNSUPPORTED_WIDGET_TYPES:
            problems.append(
                f"required field '{name}' ({widget_type}) cannot be filled via the open API; "
                "please add it in Feishu and submit there"
            )
            continue
        if widget_id not in provided:
            problems.append(f"missing required field '{name}' ({widget_type})")
            continue
        if widget_type.lower() != "fieldlist" and _is_empty_approval_value(values.get(widget_id)):
            problems.append(f"required field '{name}' ({widget_type}) cannot be empty")
            continue
        if widget_type.lower() == "fieldlist":
            rows = values.get(widget_id) or []
            if not rows:
                problems.append(f"required field '{name}' needs at least one row")
            required_children = [
                (cid, index.get(cid) or {})
                for cid in entry.get("children", [])
                if (index.get(cid) or {}).get("required")
            ]
            for position, row in enumerate(rows, start=1):
                keys = _approval_group_keys(row)
                for child_id, child in required_children:
                    if child_id not in keys:
                        problems.append(
                            f"'{name}' row {position} is missing required '{child.get('name') or child_id}'"
                        )
    return problems


def _is_empty_approval_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, Mapping) and "value" in value:
        return _is_empty_approval_value(value.get("value"))
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _is_account_widget_value(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(approval_account_number(value))


def approval_account_widgets(instance: Mapping[str, Any]) -> list[NestedDict]:
    r"""
    从已读取的审批实例表单中返回已填写的 ``account``（收款账户）控件。

    飞书没有枚举用户绑定收款账户的公开 API，但用户本人过去提交的实例可能在 `form` 中携带完整账户值。
    这些值应视为敏感数据：调用方只应暴露不透明句柄 / 脱敏标签，并且只能在同一用户的 `account`
    控件提交路径中复用完整对象。

    Examples:
        >>> import json
        >>> inst = {"form": json.dumps([
        ...     {"id": "w", "type": "account", "value": {"widgetAccountNumber": "62306157601119"}},
        ...     {"id": "x", "type": "input", "value": "hi"}])}
        >>> [w["id"] for w in approval_account_widgets(inst)]
        ['w']
    """
    parsed = _loads_json(instance.get("form")) if isinstance(instance, Mapping) else None
    widgets: list[NestedDict] = []
    for item in parsed if isinstance(parsed, list) else []:
        if isinstance(item, Mapping) and item.get("type") == "account" and item.get("value"):
            widgets.append(NestedDict(item))
    return widgets


def approval_instance_participant_ids(instance: Mapping[str, Any]) -> set[str]:
    r"""
    收集审批实例中所有合法参与者的用户标识。

    返回申请人（顶层字段）、`task_list` 中每个审批人、以及 `timeline` 中每个操作人的 `open_id` /
    `user_id`。调用方用它保证只有实例参与者可以读取该实例：查询实例接口走租户 token（用户 token 会返回
    99991668），如果没有这层校验，被越权操控的智能体可凭实例 ID 读取他人实例及其中的银行账户表单数据。
    应与请求用户自身 ID 求交集，无交集时 fail closed。

    Examples:
        >>> inst = {"open_id": "ou_a", "user_id": "alice",
        ...         "task_list": [{"open_id": "ou_b", "user_id": "bob"}],
        ...         "timeline": [{"open_id": "ou_c"}]}
        >>> sorted(approval_instance_participant_ids(inst))
        ['alice', 'bob', 'ou_a', 'ou_b', 'ou_c']
    """
    ids: set[str] = set()

    def add(node: Any) -> None:
        if isinstance(node, Mapping):
            for key in ("open_id", "user_id"):
                value = node.get(key)
                if isinstance(value, str) and value:
                    ids.add(value)

    if isinstance(instance, Mapping):
        add(instance)  # the applicant
        for task in instance.get("task_list") or []:
            add(task)
        for entry in instance.get("timeline") or []:
            add(entry)
    return ids


def approval_account_number(value: Any) -> str | None:
    r"""从账户控件值中提取银行卡号；这是服务端内部去重键，不应暴露给模型。"""
    if isinstance(value, Mapping):
        number = value.get("widgetAccountNumber")
        if isinstance(number, str) and number.strip():
            return number.strip()
    return None


def approval_account_label(value: Any) -> str:
    r"""
    为账户值生成隐私脱敏的人类可读标签：``'<bank> ****<last4> (<holder>)'``。

    仅暴露末 4 位；完整卡号绝不能进入模型上下文。

    Examples:
        >>> approval_account_label({"widgetAccountNumber": "623061576011198383", "widgetAccountName": "张三",
        ...     "widgetAccountBankName": {"value": "HZCB", "text": '{"bankNameZh": "杭州银行"}'}})
        '杭州银行 ****8383 (张三)'
    """
    if not isinstance(value, Mapping):
        return "账户"
    number = value.get("widgetAccountNumber") or ""
    holder = value.get("widgetAccountName") or ""
    bank = _account_bank_name(value.get("widgetAccountBankName"))
    tail = f"****{number[-4:]}" if isinstance(number, str) and len(number) >= 4 else "账户"
    label = " ".join(part for part in (bank, tail) if part)
    return f"{label} ({holder})" if holder else label


def _account_bank_name(field: Any) -> str:
    if isinstance(field, Mapping):
        text = _loads_json(field.get("text"))
        if isinstance(text, Mapping):
            for key in ("bankNameZh", "bankNameEn", "name"):
                val = text.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        code = field.get("value")
        if isinstance(code, str) and code.strip():
            return code.strip()
    return ""


def approval_definition_schema(definition: Mapping[str, Any]) -> NestedDict:
    r"""
    从审批定义返回紧凑、适合模型读取的 schema。

    返回值保留高价值原始定义字段，并在能发现表单控件时增加归一化 `fields` 列表。

    Args:
        definition: 原始审批定义映射，通常来自
            [feishu.approval.definitions.DefinitionsNamespace.get][]。

    Returns:
        含 `approval_name`、`status`、`node_list`、`form`、`form_widget_relation` 等原始字段的
        [chanfig.NestedDict][]；发现表单控件时增加归一化 `fields` 列表，`form` 非合法 JSON 时增加
        `form_parse_error`。
    """
    data = NestedDict(
        approval_name=definition.get("approval_name"),
        status=definition.get("status"),
        node_list=definition.get("node_list"),
        form=definition.get("form"),
        form_widget_relation=definition.get("form_widget_relation"),
    )
    widgets = approval_definition_widgets(definition)
    if widgets:
        data.fields = [_approval_widget_summary(widget) for widget in widgets]
    form = data.get("form")
    if isinstance(form, str):
        try:
            json.loads(form)
        except ValueError:
            data.form_parse_error = "form is not valid JSON"
    return data


def approval_cached_definition_summary(definition: Mapping[str, Any]) -> NestedDict:
    r"""
    返回适合缓存、日志和工具上下文的小型审批定义摘要。

    Args:
        definition: 原始或已归一化的审批定义映射（通常为
            [feishu.approval.approval_definition_schema][] 的输出）。

    Returns:
        仅包含 `approval_name`、`status`、`fields` 中已存在（非空）键的
        [chanfig.NestedDict][]。
    """
    summary = NestedDict()
    for key in ("approval_name", "status", "fields"):
        value = definition.get(key)
        if value:
            summary[key] = value
    return summary


def approval_definition_code(item: Mapping[str, Any]) -> str | None:
    r"""
    从常见飞书响应结构中提取审批定义 Code。

    Args:
        item: 可能携带审批定义 Code 的映射（列表项或定义详情）。

    Returns:
        首个命中的非空 Code（依次尝试 `approval_code`、`approvalCode`、`code`、
        `definition_code`、`definitionCode`）；均无则返回 `None`。
    """
    for key in ("approval_code", "approvalCode", "code", "definition_code", "definitionCode"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def approval_definition_summary(item: Mapping[str, Any], access_method: str | None = None) -> NestedDict:
    r"""
    将一个审批定义列表项归一化为紧凑摘要。

    Args:
        item: 单个审批定义列表项映射。
        access_method: 可选的访问方式标记（如 `"tenant_access_token"`），非空时写入返回值的
            `access_method` 字段。

    Returns:
        含 `access_method`（若提供）、`approval_code`、`approval_name`，以及 `description`、
        `group_name`、`status`、`form` 等已存在（非空）字段的 [chanfig.NestedDict][]。
    """
    summary = NestedDict()
    if access_method:
        summary.access_method = access_method
    code = approval_definition_code(item)
    name = _approval_definition_name(item)
    if code:
        summary.approval_code = code
    if name:
        summary.approval_name = name
    for key in ("description", "group_name", "groupName", "status", "form", "form_widget_relation"):
        value = item.get(key)
        if value:
            summary[key] = value
    return summary


def approval_nonempty_form(form: Any) -> Mapping[str, Any] | list[Any] | None:
    r"""
    返回非空审批表单载荷，并保留原始的映射 / 列表形状。

    与按定义把 `widget_id -> value` 序列化为控件载荷列表的
    [feishu.approval.approval_form_payloads][] 不同，本函数不做任何序列化，仅在 `form`
    为非空映射或非空列表时原样透传（映射会包装为 [chanfig.NestedDict][]），否则返回 `None`。

    Args:
        form: 待检查的原始表单，通常为映射或列表；其他类型或空值一律视为无表单。

    Returns:
        非空映射（包装为 `NestedDict`）或非空列表；当 `form` 为空或非映射 / 非列表时返回 `None`。
    """
    if isinstance(form, Mapping) and form:
        return NestedDict(form)
    if isinstance(form, list) and form:
        return form
    return None


def approval_definition_widgets(definition: Mapping[str, Any]) -> list[NestedDict]:
    r"""
    从审批定义中返回拍平后的表单控件列表。

    飞书审批定义在不同版本中会以多种形态暴露 `form`：有时是 JSON 字符串，有时是列表 / 字典。
    本函数解析已知形态，并递归收集看起来像表单控件的对象。
    """
    form = definition.get("form")
    parsed = _loads_json(form)
    widgets: list[NestedDict] = []
    _collect_widgets(parsed, widgets)
    return widgets


def approval_file_fields(definition: Mapping[str, Any] | None) -> set[str]:
    r"""
    提取文件类审批控件的稳定字段标识。

    当定义看起来包含文件控件但没有可解析的 `fields` 时，抛出 `ValueError`，
    避免调用方猜测字段名。
    """
    fields: set[str] = set()
    if not isinstance(definition, Mapping):
        return fields
    widgets = definition.get("fields")
    if not isinstance(widgets, list):
        if approval_definition_may_contain_file_widget(definition):
            raise ValueError("approval definition may contain file widgets but did not expose parsed fields")
        return fields
    for widget in widgets:
        if not isinstance(widget, Mapping) or not is_approval_file_widget(widget):
            continue
        widget_fields_before = len(fields)
        for key in ("id", "widget_id", "widgetId", "custom_id", "customId", "name"):
            value = widget.get(key)
            if isinstance(value, str) and value:
                fields.add(value)
        if len(fields) == widget_fields_before:
            raise ValueError("approval file widget does not expose a stable field id")
    return fields


def approval_definition_may_contain_file_widget(definition: Mapping[str, Any]) -> bool:
    r"""
    判断原始审批定义字段是否提到文件类控件。

    Args:
        definition: 原始审批定义映射；检查其 `form` 与 `form_widget_relation` 字段的文本表示。

    Returns:
        当任一字段的文本看起来描述文件类控件（见
        [feishu.approval.is_approval_file_widget_text][]）时返回 `True`，否则返回 `False`。
    """
    for key in ("form", "form_widget_relation"):
        value = definition.get(key)
        if isinstance(value, str) and is_approval_file_widget_text(value):
            return True
        if isinstance(value, (Mapping, list)) and is_approval_file_widget_text(str(value)):
            return True
    return False


def is_approval_file_widget_text(value: str) -> bool:
    r"""
    判断自由文本是否像是在描述审批文件类控件。

    Args:
        value: 待检查的自由文本（如序列化后的 `form` 字符串）。

    Returns:
        文本（不区分大小写）包含 `file`、`attachment`、`image`、`photo`、`upload` 或
        `附件` / `图片` / `文件` 等标记时返回 `True`，否则返回 `False`。
    """
    lowered = value.lower()
    return any(
        marker in lowered for marker in ("file", "attachment", "image", "photo", "upload", "附件", "图片", "文件")
    )


def approval_field_key(value: Mapping[str, Any]) -> str | None:
    r"""
    从表单 / 控件映射中提取最稳定的字段键。

    Args:
        value: 单个表单或控件映射。

    Returns:
        首个命中的非空字段键（依次尝试 `id`、`widget_id`、`widgetId`、`custom_id`、
        `customId`、`name`）；均无则返回 `None`。
    """
    for key in ("id", "widget_id", "widgetId", "custom_id", "customId", "name"):
        item = value.get(key)
        if isinstance(item, str) and item:
            return item
    return None


def is_approval_file_widget(value: Mapping[str, Any]) -> bool:
    r"""
    判断表单 / 控件映射是否代表文件类控件。

    Args:
        value: 单个表单或控件映射；读取其 `type` / `widget_type` / `widgetType` 控件类型。

    Returns:
        控件类型（不区分大小写）包含 `file`、`attachment`、`image`、`photo` 或 `upload`
        时返回 `True`；无法识别控件类型时返回 `False`。
    """
    widget_type = value.get("type") or value.get("widget_type") or value.get("widgetType")
    if not isinstance(widget_type, str):
        return False
    lowered = widget_type.lower()
    return any(marker in lowered for marker in ("file", "attachment", "image", "photo", "upload"))


def _approval_widget_summary(widget: Mapping[str, Any]) -> NestedDict:
    summary = NestedDict()
    for target, keys in {
        "id": ("id", "widget_id", "widgetId"),
        "custom_id": ("custom_id", "customId"),
        "name": ("name", "title", "label"),
        "type": ("type", "widget_type", "widgetType"),
    }.items():
        value = _first_present(widget, *keys)
        if value:
            summary[target] = value
    if "required" in widget:
        summary.required = bool(widget.get("required"))

    widget_type = summary.get("type")
    is_selection = _is_selection_widget_type(widget_type)
    options = _approval_widget_options(widget) if is_selection else []
    if options:
        summary.options = options
        summary.option_count = len(options)
        if len(options) == 1:
            summary.value_policy = "single_option_available; use this option without asking the user"
            value = options[0].get("value")
            if value is not None:
                summary.suggested_value = value
        else:
            summary.value_policy = "multiple_options_available; ask the user to choose one of these options"
    elif _is_selection_widget_type(summary.get("type")):
        summary.value_policy = (
            "selection_widget_without_exposed_options; do not ask for a free-form value. "
            "If the user has exactly one/default configured option, use it without asking. "
            "Only ask the user to identify which existing configured option to use when they have multiple options."
        )
    else:
        constraints = _approval_widget_constraints(widget)
        if constraints:
            summary.constraints = constraints

    # Tell the model upfront (when it reads the definition) about controls it cannot or should compute,
    # so it never builds a doomed form: API-unsupported controls must be completed by the user in Feishu;
    # account widgets require a resolver handle; formula widgets are computed totals, not free-form values.
    type_text = summary.get("type") or ""
    if type_text in APPROVAL_API_UNSUPPORTED_WIDGET_TYPES:
        summary.api_supported = False
        summary.value_policy = (
            "this control cannot be submitted via the open API; do not try to fill it — tell the user to "
            "complete this field in Feishu and submit there"
        )
    elif type_text.lower() == "account":
        summary.value_policy = (
            "payment account: do not put a raw value in form; pass a value resolved by a trusted "
            "payment-account resolver for this widget id"
        )
    elif type_text.lower() == "formula":
        summary.value_policy = (
            "computed total: submit the calculated numeric result (e.g. the sum of the related amount fields), "
            "not a placeholder; it must equal the definition's formula or Feishu rejects it"
        )
    return summary


def _approval_widget_options(widget: Mapping[str, Any]) -> list[NestedDict]:
    for key in ("options", "option"):
        value = widget.get(key)
        options = _option_list(value)
        if options:
            return options
    return []


def _approval_widget_constraints(widget: Mapping[str, Any]) -> Any:
    value = widget.get("options") if "options" in widget else widget.get("option")
    if isinstance(value, Mapping) and value:
        return value
    return None


def _option_list(value: Any) -> list[NestedDict]:
    if isinstance(value, list):
        return [option for item in value if (option := _option_summary(item))]
    if isinstance(value, Mapping):
        options: list[NestedDict] = []
        for item in value.values():
            if isinstance(item, list):
                options.extend(option for raw in item if (option := _option_summary(raw)))
        return options
    return []


def _option_summary(value: Any) -> NestedDict | None:
    if isinstance(value, str | int | float | bool):
        text = str(value)
        return NestedDict(label=text, value=text)
    if not isinstance(value, Mapping):
        return None

    option = NestedDict()
    label = _localized_text(_first_present(value, "text", "name", "label", "title", "display_name", "displayName"))
    raw_value = _first_present(value, "value", "id", "key", "code", "option_id", "optionId")
    if label:
        option.label = label
    if raw_value is not None:
        option.value = raw_value
    if not option:
        option.raw = value
    return option


def _is_selection_widget_type(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(marker in lowered for marker in ("radio", "checkbox", "select", "account", "contact", "connect"))


def _format_approval_value(
    index: Mapping[str, Mapping[str, Any]],
    widget_type: str | None,
    value: Any,
    *,
    default_currency: str,
    tz_offset: str,
) -> tuple[Any, dict[str, Any]]:
    r"""按飞书控件类型格式化单个值，返回 ``(value, extra_siblings)``，如金额控件的货币字段。"""
    kind = (widget_type or "").lower()
    if kind == "amount":
        currency = default_currency
        number = value
        if isinstance(value, Mapping):
            number = _first_present(value, "value", "amount", "number")
            currency = value.get("currency") or currency
        return _approval_number(number), {"currency": currency}
    if kind in ("formula", "number"):
        return _approval_number(value), {}
    if kind == "date":
        return _approval_date(value, tz_offset), {}
    if kind == "fieldlist":
        groups: list[Any] = []
        for row in value if isinstance(value, list) else []:
            items: list[dict[str, Any]] = []
            for child_id, child_value in _approval_group_items(row):
                child_type = (index.get(child_id) or {}).get("type")
                formatted, extra = _format_approval_value(
                    index, child_type, child_value, default_currency=default_currency, tz_offset=tz_offset
                )
                child_item: dict[str, Any] = {"id": child_id}
                if child_type:
                    child_item["type"] = child_type
                child_item["value"] = formatted
                child_item.update(extra)
                items.append(child_item)
            groups.append(items)
        return groups, {}
    if kind in _APPROVAL_LIST_VALUE_TYPES:
        return (value if isinstance(value, list) else [value]), {}
    return value, {}


def _approval_number(value: Any) -> Any:
    r"""将看起来像数字的值转为 int / float；飞书金额 / 公式控件需要 JSON 数字，其余值原样返回。"""
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        try:
            number = float(value.strip().replace(",", ""))
        except ValueError:
            return value
        return int(number) if number.is_integer() else number
    return value


def _approval_date(value: Any, tz_offset: str) -> Any:
    r"""将裸 ``YYYY-MM-DD`` 补为带偏移的 RFC3339；实例日期控件需要时间和时区，其余值原样返回。"""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if "T" in text:
        return text
    if len(text) == 10 and text[4] == "-" and text[7] == "-" and text.replace("-", "").isdigit():
        return f"{text}T00:00:00{tz_offset}"
    return text


def _approval_group_items(row: Any) -> list[tuple[str, Any]]:
    r"""
    将 fieldList 行（`child_id -> value` 映射或 ``{id, value}`` 列表）归一化为 ``(child_id, value)``。
    """
    if isinstance(row, Mapping):
        return [(str(key), val) for key, val in row.items()]
    if isinstance(row, list):
        pairs: list[tuple[str, Any]] = []
        for item in row:
            if isinstance(item, Mapping) and item.get("id") is not None:
                pairs.append((str(item["id"]), item.get("value")))
        return pairs
    return []


def _approval_group_keys(row: Any) -> set[str]:
    r"""返回 fieldList 行中出现的子控件 ID，兼容两种行形状。"""
    return {key for key, _ in _approval_group_items(row)}


def _child_id(child: Mapping[str, Any]) -> str | None:
    return _first_string(NestedDict(child), "id", "widget_id", "widgetId", "custom_id", "customId")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _loads_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _collect_widgets(value: Any, widgets: list[NestedDict]) -> None:
    value = _loads_json(value)
    if isinstance(value, list):
        for item in value:
            _collect_widgets(item, widgets)
        return
    if not isinstance(value, Mapping):
        return

    data = NestedDict(value)
    widget_id = _first_string(data, "id", "widget_id", "widgetId", "custom_id", "customId")
    widget_type = _first_string(data, "type", "widget_type", "widgetType")
    name = _localized_name(data)
    if widget_id and (widget_type or name):
        widget = NestedDict(id=widget_id)
        if widget_type:
            widget.type = widget_type
        if name:
            widget.name = name
        for key in ("custom_id", "customId", "required", "option", "options"):
            if key in data:
                widget[key] = data[key]
        widgets.append(widget)

    for key in (
        "children",
        "fields",
        "items",
        "props",
        "value",
        "widgets",
        "widget_list",
        "widgetList",
        "form",
        "components",
    ):
        if key in data:
            _collect_widgets(data[key], widgets)


def _first_string(data: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _first_present(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _localized_name(data: Mapping[str, Any]) -> str | None:
    for key in ("name", "title", "label"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, Mapping):
            for locale_key in ("zh_cn", "zh-CN", "zh", "en_us", "en-US", "en"):
                item = value.get(locale_key)
                if isinstance(item, str) and item:
                    return item
    return None


def _localized_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("zh_cn", "zh-CN", "zh", "en_us", "en-US", "en"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def _approval_definition_name(item: Mapping[str, Any]) -> str | None:
    for key in ("approval_name", "approvalName", "name", "title"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, Mapping):
            text = _joined_strings(value, limit=6)
            if text:
                return text
    return None


def _joined_strings(value: Any, *, limit: int) -> str:
    strings: list[str] = []
    _collect_strings(value, strings, limit=limit)
    return " ".join(strings)


def _collect_strings(value: Any, strings: list[str], *, limit: int) -> None:
    if len(strings) >= limit:
        return
    if isinstance(value, str):
        if value.strip():
            strings.append(value.strip())
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _collect_strings(item, strings, limit=limit)
            if len(strings) >= limit:
                return
        return
    if isinstance(value, list | tuple):
        for item in value:
            _collect_strings(item, strings, limit=limit)
            if len(strings) >= limit:
                return
