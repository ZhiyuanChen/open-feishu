"""Base-URL and accounts-host resolution.

Base-URL resolution is observed through the public ``FeishuClient.base_url``.
The default/lark accounts hosts and the unknown-region raise are covered
behaviorally through ``client.oauth.authorize_url`` in test_oauth_authorize_url.py;
the only accounts path not exercised there is an explicit accounts-host override,
asserted directly here via ``resolve_accounts_url``.
"""

import pytest

from feishu import FeishuClient
from feishu.consts import resolve_accounts_url


class TestBaseURLResolution:
    @pytest.mark.parametrize(
        "kwargs, expected",
        [
            ({}, "https://open.feishu.cn"),
            ({"region": "lark"}, "https://open.larksuite.com"),
            ({"base_url": "https://example.com/"}, "https://example.com"),
        ],
    )
    def test_base_url(self, kwargs, expected):
        client = FeishuClient("cli_a", "s", **kwargs)
        assert client.base_url == expected

    def test_unknown_region_raises(self):
        with pytest.raises(ValueError):
            FeishuClient("cli_a", "s", region="nope")


class TestAccountsURLResolution:
    def test_override_wins(self):
        assert resolve_accounts_url("feishu", "https://accounts.internal.example.com/") == (
            "https://accounts.internal.example.com"
        )
