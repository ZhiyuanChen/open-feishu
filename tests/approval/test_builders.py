import json

from feishu.approval import (
    approval_definition_code,
    approval_definition_schema,
    approval_definition_summary,
    approval_field_key,
    approval_file_fields,
    approval_form_payload,
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


def test_approval_form_payload_preserves_non_empty_shapes():
    assert approval_form_payload({"amount": 10}).amount == 10
    assert approval_form_payload([{"id": "amount", "value": 10}]) == [{"id": "amount", "value": 10}]
    assert approval_form_payload({}) is None


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
