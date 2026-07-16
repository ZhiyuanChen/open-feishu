import json

from feishu.agent.payment_accounts import PaymentAccountResolver, payment_account_handle

_ACCOUNT_VALUE = {
    "widgetAccountNumber": "623061576011198383",
    "widgetAccountName": "张三",
    "widgetAccountBankName": {"value": "HZCB", "text": '{"bankNameZh": "杭州银行"}'},
}

_POLLUTED_ACCOUNT_VALUE = {
    "widgetAccountNumber": _ACCOUNT_VALUE["widgetAccountNumber"],
    "widgetAccountName": _ACCOUNT_VALUE["widgetAccountName"],
    "widgetAccountType": {
        "value": "map[text:map[text:1 value:1] value:map[text:1 value:1]]",
        "text": "map[text:map[text:1 value:1] value:map[text:1 value:1]]",
    },
    "widgetAccountBankArea": {
        "value": (
            'map[text:map[text:[{"name":"China"}] value:1814991] ' 'value:map[text:[{"name":"China"}] value:1814991]]'
        ),
        "text": (
            'map[text:map[text:[{"name":"China"}] value:1814991] ' 'value:map[text:[{"name":"China"}] value:1814991]]'
        ),
    },
    "widgetAccountBankName": {
        "value": "",
        "text": "[123 34 98 97 110 107 67 111 100 101 34 58 34 34 125]",
    },
    "widgetAccountBankBranch": {"value": "175950", "text": '{"bankBranchNameZh": "北京海淀黄庄支行"}'},
}


class _Instances:
    def __init__(self, *, query_codes=None, instances=None):
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
        if instances is not None:
            self.instances.update(instances)
        self.query_codes = query_codes or ["own", "other"]

    async def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return [{"instance": {"code": code}} for code in self.query_codes]

    async def get(self, code):
        self.get_calls.append(code)
        return self.instances[code]


class _Approval:
    def __init__(self, *, query_codes=None, instances=None):
        self.instances = _Instances(query_codes=query_codes, instances=instances)


class _Client:
    def __init__(self, *, query_codes=None, instances=None):
        self.approval = _Approval(query_codes=query_codes, instances=instances)


async def test_lists_own_accounts():
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

    assert await resolver.resolve(user, account_id) == _ACCOUNT_VALUE


async def test_requires_open_id():
    client = _Client()
    resolver = PaymentAccountResolver(client)

    assert await resolver.recent({"user_id": "u_1"}) == []
    assert client.approval.instances.query_calls == []


async def test_skips_invalid_accounts():
    client = _Client(
        query_codes=["own_polluted", "own"],
        instances={
            "own_polluted": {
                "open_id": "ou_1",
                "form": json.dumps([{"id": "bank", "type": "account", "value": _POLLUTED_ACCOUNT_VALUE}]),
            },
        },
    )
    resolver = PaymentAccountResolver(client)
    user = {"open_id": "ou_1", "user_id": "u_1"}

    accounts = await resolver.recent(user, approval_code="APPROVAL", limit=10)

    account_id = payment_account_handle(_ACCOUNT_VALUE["widgetAccountNumber"])
    assert [account.summary() for account in accounts] == [
        {"account_id": account_id, "label": "杭州银行 ****8383 (张三)"}
    ]
    assert await resolver.resolve(user, account_id) == _ACCOUNT_VALUE
