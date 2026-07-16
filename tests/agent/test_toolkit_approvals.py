import json

import pytest

from feishu.agent.context import ToolContext, use_tool_context
from feishu.agent.payment_accounts import PaymentAccount
from feishu.agent.result import ToolOutcome
from feishu.agent.toolkit.approvals import (
    approve_approval_task,
    cancel_approval_instance,
    create_approval_instance,
    get_approval_status,
    list_my_payment_accounts,
    reject_approval_task,
)

_ACCOUNT_VALUE = {
    "widgetAccountNumber": "623061576011198383",
    "widgetAccountName": "张三",
    "widgetAccountBankName": {"value": "HZCB", "text": '{"bankNameZh": "杭州银行"}'},
}


class _Definitions:
    async def get(self, approval_code, *, locale=None):
        return {
            "form": json.dumps(
                [
                    {"id": "reason", "type": "textarea", "name": "事由", "required": True},
                    {"id": "bank", "type": "account", "name": "收款账户", "required": True},
                ],
                ensure_ascii=False,
            )
        }


class _Instances:
    def __init__(self):
        self.cancel_calls = []
        self.create_calls = []
        self.instances = {}

    async def create(self, payload):
        self.create_calls.append(payload)
        return {"instance_code": "INSTANCE"}

    async def get(self, instance_code):
        return self.instances[instance_code]

    async def cancel(self, approval_code, instance_code, user_id, *, user_id_type=None):
        self.cancel_calls.append((approval_code, instance_code, user_id, user_id_type))
        return {"ok": True}


class _Approval:
    def __init__(self):
        self.definitions = _Definitions()
        self.instances = _Instances()
        self.tasks = _Tasks()


class _Tasks:
    def __init__(self):
        self.approve_calls = []
        self.reject_calls = []

    async def approve(self, task):
        self.approve_calls.append(task)
        return {"ok": True}

    async def reject(self, task):
        self.reject_calls.append(task)
        return {"ok": True}


class _Client:
    def __init__(self):
        self.approval = _Approval()


class _PaymentAccounts:
    def __init__(self):
        self.recent_calls = []
        self.resolve_calls = []

    async def recent(self, user, *, approval_code=None, limit=10):
        self.recent_calls.append((dict(user), approval_code, limit))
        return [
            PaymentAccount(
                account_id="pa_1",
                label="杭州银行 ****8383 (张三)",
                account_value=dict(_ACCOUNT_VALUE),
                user_keys=("ou_1",),
            )
        ]

    async def resolve(self, user, account_id):
        self.resolve_calls.append((dict(user), account_id))
        if account_id == "pa_1":
            return dict(_ACCOUNT_VALUE)
        return None


class TestApprovalToolkit:
    def test_create_approval_instance_accepts_payment_accounts(self):
        tool = create_approval_instance(description="create")

        assert tool.input_schema["properties"]["accounts"]["additionalProperties"] == {"type": "string"}

    async def test_create_approval_instance_resolves_payment_account_handle(self):
        client = _Client()
        accounts = _PaymentAccounts()
        tool = create_approval_instance(description="create")

        with use_tool_context(ToolContext(client=client, user={"open_id": "ou_1"}, payment_accounts=accounts)):
            result = await tool.handler(
                approval_code="APPROVAL",
                form={"reason": "打车报销"},
                accounts={"bank": "pa_1"},
            )

        assert result.outcome is ToolOutcome.COMPLETED
        assert accounts.resolve_calls == [({"open_id": "ou_1"}, "pa_1")]
        [payload] = client.approval.instances.create_calls
        form = json.loads(payload.form)
        assert {"id": "bank", "type": "account", "value": _ACCOUNT_VALUE} in form
        assert {"id": "reason", "type": "textarea", "value": "打车报销"} in form

    async def test_create_approval_instance_accepts_unique_field_names(self):
        client = _Client()
        accounts = _PaymentAccounts()
        tool = create_approval_instance(description="create")

        with use_tool_context(ToolContext(client=client, user={"open_id": "ou_1"}, payment_accounts=accounts)):
            result = await tool.handler(
                approval_code="APPROVAL",
                form={"事由": "打车报销"},
                accounts={"收款账户": "pa_1"},
            )

        assert result.outcome is ToolOutcome.COMPLETED
        [payload] = client.approval.instances.create_calls
        form = json.loads(payload.form)
        assert {"id": "bank", "type": "account", "value": _ACCOUNT_VALUE} in form
        assert {"id": "reason", "type": "textarea", "value": "打车报销"} in form

    async def test_create_approval_instance_rejects_raw_payment_account_in_form(self):
        client = _Client()
        accounts = _PaymentAccounts()
        tool = create_approval_instance(description="create")

        with use_tool_context(ToolContext(client=client, user={"open_id": "ou_1"}, payment_accounts=accounts)):
            result = await tool.handler(
                approval_code="APPROVAL",
                form={"reason": "打车报销", "bank": dict(_ACCOUNT_VALUE)},
            )

        assert result.outcome is ToolOutcome.FAILED
        assert "raw account values in form are not accepted" in result.content
        assert client.approval.instances.create_calls == []
        assert accounts.resolve_calls == []

    async def test_get_approval_status_redacts_payment_account_values(self):
        client = _Client()
        client.approval.instances.instances["INSTANCE"] = {
            "instance_code": "INSTANCE",
            "open_id": "ou_1",
            "form": json.dumps(
                [
                    {"id": "reason", "type": "textarea", "value": "打车报销"},
                    {"id": "bank", "type": "account", "value": dict(_ACCOUNT_VALUE)},
                ],
                ensure_ascii=False,
            ),
        }
        tool = get_approval_status(description="status")

        with use_tool_context(ToolContext(client=client, user={"open_id": "ou_1"})):
            result = await tool.handler(instance_code="INSTANCE")

        assert result.outcome is ToolOutcome.COMPLETED
        content = json.dumps(result.content, ensure_ascii=False)
        assert "widgetAccountNumber" not in content
        assert _ACCOUNT_VALUE["widgetAccountNumber"] not in content
        assert "杭州银行 ****8383 (张三)" in content

    async def test_list_my_payment_accounts_returns_masked_summaries(self):
        accounts = _PaymentAccounts()
        tool = list_my_payment_accounts(description="accounts")

        with use_tool_context(ToolContext(user={"open_id": "ou_1"}, payment_accounts=accounts)):
            result = await tool.handler(approval_code="APPROVAL", limit=1)

        assert result.outcome is ToolOutcome.COMPLETED
        assert result.content == [{"account_id": "pa_1", "label": "杭州银行 ****8383 (张三)"}]
        assert accounts.recent_calls == [({"open_id": "ou_1"}, "APPROVAL", 1)]

    async def test_cancel_approval_instance_uses_open_id_requester(self):
        client = _Client()
        tool = cancel_approval_instance(description="cancel")

        with use_tool_context(ToolContext(client=client, user={"open_id": "ou_1"})):
            result = await tool.handler(approval_code="APPROVAL", instance_code="INSTANCE")

        assert result.outcome is ToolOutcome.COMPLETED
        assert client.approval.instances.cancel_calls == [("APPROVAL", "INSTANCE", "ou_1", "open_id")]

    @pytest.mark.parametrize(
        "factory, kwargs",
        [
            pytest.param(
                create_approval_instance,
                {"approval_code": "APPROVAL", "form": {}},
                id="create",
            ),
            pytest.param(
                cancel_approval_instance,
                {"approval_code": "APPROVAL", "instance_code": "INSTANCE"},
                id="cancel",
            ),
            pytest.param(
                approve_approval_task,
                {"approval_code": "APPROVAL", "instance_code": "INSTANCE", "task_id": "TASK"},
                id="approve",
            ),
            pytest.param(
                reject_approval_task,
                {"approval_code": "APPROVAL", "instance_code": "INSTANCE", "task_id": "TASK"},
                id="reject",
            ),
        ],
    )
    async def test_approval_writes_reject_union_id_only_requester(self, factory, kwargs):
        client = _Client()
        tool = factory(description="write")

        with use_tool_context(ToolContext(client=client, user={"union_id": "on_1"})):
            result = await tool.handler(**kwargs)

        assert result.outcome is ToolOutcome.BLOCKED
