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
    Build one Feishu approval form widget value.

    Args:
        widget_id: Form widget ID from the approval definition.
        value: Value accepted by that widget.
        widget_type: Optional Feishu widget type.
        name: Optional readable field name.

    Returns:
        A form widget payload.

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
    Serialize Feishu approval form fields.

    Args:
        fields: Either a mapping of `widget_id -> value`, or an iterable of full widget payloads.

    Returns:
        JSON string suitable for the `form` field of `approval/v4/instances`.

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
    Build a Feishu approval instance creation payload.

    Args:
        approval_code: Approval definition code.
        form: Serialized form JSON, a `widget_id -> value` mapping, or full widget payloads.
        user_id: Applicant user ID.
        open_id: Applicant open ID.
        department_id: Applicant department ID.
        node_approver_user_id_list: Optional custom approver list.
        extra: Additional Feishu fields to include.

    Returns:
        Approval instance payload for [feishu.approval.instances.InstancesNamespace.create][].
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


# Controls Feishu's open approval API documents as UNSUPPORTED for instance creation: they cannot be
# filled via `approval/v4/instances` and must be handled in the Feishu approval admin backend / UI.
# NOTE: `account` (收款账户) is deliberately NOT here — despite the docs listing it as unsupported, an
# account value is a plain self-contained object (not a server token), so it CAN be resubmitted verbatim
# from a user's own prior instance (see feishu.agent.payment_accounts). Required widgets of these types
# still make a form un-submittable via API.
APPROVAL_API_UNSUPPORTED_WIDGET_TYPES: frozenset[str] = frozenset({"text", "mutableGroup", "serialNumber", "tripGroup"})

# Widget types whose submitted value must be a LIST (wrap a single value), per the create-instance contract.
_APPROVAL_LIST_VALUE_TYPES: frozenset[str] = frozenset(
    {"checkbox", "checkboxv2", "multiselect", "attachment", "attachmentv2", "image", "imagev2", "contact", "connect"}
)


def approval_definition_index(definition: Mapping[str, Any]) -> dict[str, NestedDict]:
    r"""
    Index an approval definition's form into ``widget_id -> {type, name, required, is_child, children}``.

    Unlike [feishu.approval.approval_definition_widgets][] (which flattens), this preserves the parent/child
    relationship of ``fieldList`` (费用明细 等可重复分组) widgets: a fieldList entry carries ``children`` (its
    ordered child widget ids) and each child entry is marked ``is_child=True``. This index drives both
    definition-aware serialization ([feishu.approval.approval_form_payloads][]) and required-coverage
    validation ([feishu.approval.approval_form_problems][]).

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
    Serialize ``widget_id -> value`` into the create-instance form payload, typed per the definition.

    Each element becomes ``{"id", "type", "value"}`` (Feishu's documented shape) with per-type formatting:
    ``amount`` → numeric ``value`` + sibling ``currency``; ``date`` → RFC3339 with ``tz_offset``;
    ``formula``/``number`` → JSON number; list-valued types (attachmentV2/checkboxV2/contact/connect) →
    arrays; ``fieldList`` → a 2D array of typed child rows. Unknown types pass the value through unchanged.

    Args:
        index: Output of [feishu.approval.approval_definition_index][] (widget id -> entry).
        values: Caller's ``widget_id -> value`` mapping (model form + merged attachment codes).
        default_currency: Currency for ``amount`` widgets when the value carries none. Default ``"CNY"``.
        tz_offset: Timezone offset appended to bare ``YYYY-MM-DD`` dates. Default ``"+08:00"``.

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


def approval_form_problems(index: Mapping[str, Mapping[str, Any]], values: Mapping[str, Any]) -> list[str]:
    r"""
    Return human-readable reasons a form would fail validation, BEFORE hitting Feishu's opaque ``1390001``.

    Checks every required top-level widget: an API-unsupported type (see
    [feishu.approval.APPROVAL_API_UNSUPPORTED_WIDGET_TYPES][], e.g. ``account``/收款账户) is reported as
    un-fillable; a missing value is reported by field name; a ``fieldList`` is checked to have at least one
    row and each row to carry its required children. An empty list means the form looks complete.

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
    for widget_id, entry in index.items():
        if entry.get("is_child") or not entry.get("required"):
            continue
        widget_type = entry.get("type") or ""
        name = entry.get("name") or widget_id
        if widget_type in APPROVAL_API_UNSUPPORTED_WIDGET_TYPES:
            problems.append(
                f"required field '{name}' ({widget_type}) cannot be filled via the open API; "
                "please add it in Feishu and submit there"
            )
            continue
        if widget_id not in provided:
            problems.append(f"missing required field '{name}' ({widget_type})")
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


def approval_account_widgets(instance: Mapping[str, Any]) -> list[NestedDict]:
    r"""
    Return the filled ``account`` (收款账户) widgets from a fetched approval INSTANCE's form.

    Feishu has no API to enumerate a user's bound payment accounts, but an instance the user previously
    submitted carries the full account value in its ``form``. Reading the user's OWN past instances
    (see [feishu.approval.instances.InstancesNamespace.query][]) is therefore the only way to recover an
    account value to reuse. The value is a self-contained object (bank area/branch/name + holder + number),
    not an opaque token, so it can be resubmitted verbatim.

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
    Collect the user ids of everyone legitimately involved in an approval instance.

    Returns the ``open_id`` / ``user_id`` of the applicant (top-level), every approver in ``task_list``, and
    every actor in ``timeline``. Callers use this to enforce that only a participant may read an instance —
    instance reads run on a tenant token (user token → 99991668), so without this check a jailbroken agent
    could fetch anyone's instance (and its bank/account form data) by id. Match against the requesting user's
    own ids and fail closed if there is no overlap.

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
    r"""Extract the bank account number from an account-control value — the natural dedup key (kept server-side)."""
    if isinstance(value, Mapping):
        number = value.get("widgetAccountNumber")
        if isinstance(number, str) and number.strip():
            return number.strip()
    return None


def approval_account_label(value: Any) -> str:
    r"""
    A privacy-masked, human label for an account value: ``'<bank> ****<last4> (<holder>)'``.

    Only the last 4 digits are ever surfaced — the full number must never reach the model.

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
    Return a compact, model-friendly schema from an approval definition.

    The returned schema keeps the high-value raw definition fields and adds a
    normalized ``fields`` list when form widgets can be discovered.
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
    Return a small summary suitable for caches, logs, and tool context.
    """
    summary = NestedDict()
    for key in ("approval_name", "status", "fields"):
        value = definition.get(key)
        if value:
            summary[key] = value
    return summary


def approval_definition_code(item: Mapping[str, Any]) -> str | None:
    r"""
    Extract an approval definition code from common Feishu response shapes.
    """
    for key in ("approval_code", "approvalCode", "code", "definition_code", "definitionCode"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def approval_definition_summary(item: Mapping[str, Any], access_method: str | None = None) -> NestedDict:
    r"""
    Normalize one approval definition list item into a compact summary.
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


def approval_form_payload(form: Any) -> Mapping[str, Any] | list[Any] | None:
    r"""
    Return a non-empty approval form payload, preserving mapping/list shape.
    """
    if isinstance(form, Mapping) and form:
        return NestedDict(form)
    if isinstance(form, list) and form:
        return form
    return None


def approval_definition_widgets(definition: Mapping[str, Any]) -> list[NestedDict]:
    r"""
    Return flattened form widgets from an approval definition.

    Feishu approval definitions expose ``form`` in a few shapes across versions:
    sometimes as a JSON string, sometimes as a list/dict. This helper parses the
    known shapes and recursively collects objects that look like form widgets.
    """
    form = definition.get("form")
    parsed = _loads_json(form)
    widgets: list[NestedDict] = []
    _collect_widgets(parsed, widgets)
    return widgets


def approval_file_fields(definition: Mapping[str, Any] | None) -> set[str]:
    r"""
    Extract stable field identifiers for file-like approval widgets.

    When a definition appears to contain file widgets but no parsed ``fields``
    are available, ``ValueError`` is raised so callers can avoid guessing.
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
    Return whether raw definition fields mention a file-like widget.
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
    Return whether free text looks like it describes an approval file widget.
    """
    lowered = value.lower()
    return any(
        marker in lowered for marker in ("file", "attachment", "image", "photo", "upload", "附件", "图片", "文件")
    )


def approval_field_key(value: Mapping[str, Any]) -> str | None:
    r"""
    Extract the most stable field key from a form/widget mapping.
    """
    for key in ("id", "widget_id", "widgetId", "custom_id", "customId", "name"):
        item = value.get(key)
        if isinstance(item, str) and item:
            return item
    return None


def is_approval_file_widget(value: Mapping[str, Any]) -> bool:
    r"""
    Return whether a form/widget mapping represents a file-like widget.
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
    # so it never builds a doomed form: API-unsupported controls (e.g. account/收款账户) must be completed
    # by the user in Feishu; formula widgets are computed totals, not free-form values.
    type_text = summary.get("type") or ""
    if type_text in APPROVAL_API_UNSUPPORTED_WIDGET_TYPES:
        summary.api_supported = False
        summary.value_policy = (
            "this control cannot be submitted via the open API; do not try to fill it — tell the user to "
            "complete this field in Feishu and submit there"
        )
    elif type_text.lower() == "account":
        summary.value_policy = (
            "payment account (收款账户): do NOT write a value here. Call list_my_payment_accounts to get the "
            "user's account handle(s), then pass {widget_id: account_id} in create_approval_instance's "
            "'accounts' argument. One account -> use it; several -> ask the user which; none -> ask the user "
            "to add an account in Feishu first"
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
    r"""Format one widget value per its Feishu type; returns ``(value, extra_siblings)`` (e.g. amount currency)."""
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
    r"""Coerce a numeric-looking value to an int/float (Feishu amount/formula want JSON numbers); else unchanged."""
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
    r"""Upgrade a bare ``YYYY-MM-DD`` to RFC3339 with offset (instance-level dates want time+offset); else unchanged."""
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
    Normalize a fieldList row (a child_id->value mapping OR a list of ``{id,value}``) to ``(child_id, value)`` pairs.
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
    r"""Return the child widget ids present in a fieldList row (either shape)."""
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
