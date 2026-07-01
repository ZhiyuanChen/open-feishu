import json

from feishu.agent.payment_accounts import PaymentAccountResolver, payment_account_handle

_ACCOUNT_VALUE = {
    "widgetAccountNumber": "623061576011198383",
    "widgetAccountName": "张三",
    "widgetAccountBankName": {"value": "HZCB", "text": '{"bankNameZh": "杭州银行"}'},
}


class _Instances:
    def __init__(self):
        self.query_calls = []
        self.get_calls = []
        self.instances = {
            "own": {
                "open_id": "ou_1",
                "form": json.dumps(
                    [
                        {"id": "bank", "type": "account", "value": _ACCOUNT_VALUE},
                        {"id": "reason", "type": "textarea", "value": "打车报销"},
                    ],
                    ensure_ascii=False,
                ),
            },
            "other": {
                "open_id": "ou_2",
                "form": json.dumps([{"id": "bank", "type": "account", "value": _ACCOUNT_VALUE}]),
            },
        }

    async def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return [{"instance": {"code": "own"}}, {"instance": {"code": "other"}}]

    async def get(self, code):
        self.get_calls.append(code)
        return self.instances[code]


class _Approval:
    def __init__(self):
        self.instances = _Instances()


class _Client:
    def __init__(self):
        self.approval = _Approval()


async def test_payment_account_resolver_lists_masked_own_accounts_and_resolves_cached_value():
    client = _Client()
    resolver = PaymentAccountResolver(client)
    user = {"open_id": "ou_1", "user_id": "u_1"}

    accounts = await resolver.recent(user, approval_code="APPROVAL", limit=10)

    account_id = payment_account_handle(_ACCOUNT_VALUE["widgetAccountNumber"])
    assert [account.summary() for account in accounts] == [
        {"account_id": account_id, "label": "杭州银行 ****8383 (张三)"}
    ]
    assert _ACCOUNT_VALUE["widgetAccountNumber"] not in str(accounts[0].summary())
    assert client.approval.instances.query_calls[0]["user_id"] == "ou_1"
    assert client.approval.instances.query_calls[0]["user_id_type"] == "open_id"
    assert client.approval.instances.query_calls[0]["approval_code"] == "APPROVAL"
    assert client.approval.instances.get_calls == ["own", "other"]

    assert await resolver.resolve(user, account_id) == _ACCOUNT_VALUE


async def test_payment_account_resolver_requires_open_id_to_scope_history_query():
    client = _Client()
    resolver = PaymentAccountResolver(client)

    assert await resolver.recent({"user_id": "u_1"}) == []
    assert client.approval.instances.query_calls == []
