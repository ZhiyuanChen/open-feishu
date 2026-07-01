import json

from feishu.approval import (
    approval_definition_code,
    approval_definition_index,
    approval_definition_schema,
    approval_definition_summary,
    approval_field_key,
    approval_file_fields,
    approval_form_problems,
    approval_nonempty_form,
)


def test_definition_schema_summarizes_selection_options():
    definition = {
        "approval_name": "Leave",
        "status": "active",
        "form": json.dumps(
            [
                {
                    "id": "leave_type",
                    "type": "radioV2",
                    "name": {"zh_cn": "请假类型"},
                    "required": True,
                    "option": [{"text": {"zh_cn": "年假"}, "value": "annual"}],
                }
            ],
            ensure_ascii=False,
        ),
    }

    schema = approval_definition_schema(definition)

    assert schema.approval_name == "Leave"
    assert schema.fields[0].id == "leave_type"
    assert schema.fields[0].name == "请假类型"
    assert schema.fields[0].required is True
    assert schema.fields[0].suggested_value == "annual"


def test_definition_schema_marks_invalid_form_json():
    schema = approval_definition_schema({"form": "not-json"})

    assert schema.form_parse_error == "form is not valid JSON"


def test_definition_summary_extracts_code_and_localized_name():
    summary = approval_definition_summary(
        {"approvalCode": "ABC", "name": {"zh_cn": "报销"}, "groupName": "Finance"},
        "tenant_access_token",
    )

    assert summary.approval_code == "ABC"
    assert summary.approval_name == "报销"
    assert summary.access_method == "tenant_access_token"
    assert approval_definition_code({"definitionCode": "XYZ"}) == "XYZ"


def test_approval_nonempty_form_preserves_non_empty_shapes():
    assert approval_nonempty_form({"amount": 10}).amount == 10
    assert approval_nonempty_form([{"id": "amount", "value": 10}]) == [{"id": "amount", "value": 10}]
    assert approval_nonempty_form({}) is None


def test_approval_file_fields_extracts_file_widget_keys():
    schema = approval_definition_schema(
        {
            "form": json.dumps(
                [
                    {"id": "proof", "type": "attachmentV2", "name": "Proof"},
                    {"id": "amount", "type": "number", "name": "Amount"},
                ]
            )
        }
    )

    assert approval_file_fields(schema) == {"proof", "Proof"}
    assert approval_field_key({"customId": "proof_custom"}) == "proof_custom"


def test_approval_form_problems_rejects_empty_required_scalar():
    definition = {
        "form": json.dumps(
            [
                {"id": "reason", "type": "textarea", "name": "Reason", "required": True},
                {"id": "amount", "type": "amount", "name": "Amount", "required": True},
            ]
        )
    }
    index = approval_definition_index(definition)

    problems = approval_form_problems(index, {"reason": "", "amount": {"value": ""}})

    assert "required field 'Reason' (textarea) cannot be empty" in problems
    assert "required field 'Amount' (amount) cannot be empty" in problems


def test_required_account_widget_requires_resolved_account_value():
    definition = {"form": json.dumps([{"id": "bank", "type": "account", "name": "收款账户", "required": True}])}
    index = approval_definition_index(definition)

    assert approval_form_problems(index, {}) == ["missing required field '收款账户' (account)"]
    assert approval_form_problems(index, {"bank": "pa_1"}) == [
        "field '收款账户' (account) must be supplied by a trusted payment-account resolver; "
        "raw account values in form are not accepted"
    ]
    account_value = {
        "widgetAccountNumber": "623061576011198383",
        "widgetAccountName": "张三",
    }
    assert approval_form_problems(index, {"bank": account_value}) == [
        "field '收款账户' (account) must be supplied by a trusted payment-account resolver; "
        "raw account values in form are not accepted"
    ]
    assert approval_form_problems(index, {"bank": account_value}, resolved_account_widget_ids={"bank"}) == []
