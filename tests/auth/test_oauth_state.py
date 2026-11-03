import pytest

from feishu.auth.oauth_state import OAuthStateSigner


@pytest.mark.parametrize("tenant,expected", [("a", True), ("b", False), (None, False)])
def test_state_matches_tenant(tenant, expected):
    signer = OAuthStateSigner("secret")
    state = signer.consume(signer.issue(user_keys=("user_id:alice",), tenant_key="a"))
    assert signer.user_matches(state, {"user_id": "alice", "tenant_key": tenant}) is expected


@pytest.mark.parametrize("tenant", ["", " "])
def test_rejects_empty_tenant(tenant):
    with pytest.raises(ValueError):
        OAuthStateSigner("secret").issue(user_keys=("user_id:alice",), tenant_key=tenant)
