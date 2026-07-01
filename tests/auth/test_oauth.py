"""OAuth (user-access-token) flows: authorize URL building, code exchange,
refresh-token rotation, and user-info lookup.

Two distinct client factories: ``authorize_url`` builds a real ``FeishuClient``
by region (no transport) for authorize_url tests; ``make_oauth_client`` builds a
client over a mock ``handler`` for the token/user-info HTTP tests. The OAuth
token endpoints do not fetch a tenant token, so conftest's token-stubbing
``make_client`` is deliberately not used here.
"""

import json as _json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from feishu import FeishuClient
from feishu.auth import OAuthStateSigner, build_oauth_redirect_uri, normalize_oauth_callback_path
from feishu.auth.credentials import Credential
from feishu.errors import FeishuApiError, FeishuAuthError


class _NonInternalCredential(Credential):
    def cache_key(self, token_type, base_url):
        return "x"

    async def fetch(self, transport, token_type):
        return ("tok", 7200)


def _query(url):
    return parse_qs(urlsplit(url).query)


def _recording_handler(seen, response):
    """Capture method/path/auth/body of a request and reply with ``response``."""

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = _json.loads(request.content or b"{}")
        return response

    return handler


class TestRedirectUriHelpers:
    @pytest.mark.parametrize(
        "path, expected",
        [
            ("oauth/callback", "/oauth/callback"),
            ("/oauth/callback", "/oauth/callback"),
            ("///oauth/callback", "/oauth/callback"),
        ],
    )
    def test_normalize_oauth_callback_path(self, path, expected):
        assert normalize_oauth_callback_path(path) == expected

    @pytest.mark.parametrize(
        "public_url, callback_path, expected",
        [
            ("https://app.example.com", "oauth/callback", "https://app.example.com/oauth/callback"),
            ("https://app.example.com/", "/oauth/callback", "https://app.example.com/oauth/callback"),
            (" https://app.example.com/ ", "oauth/callback", "https://app.example.com/oauth/callback"),
        ],
    )
    def test_build_oauth_redirect_uri(self, public_url, callback_path, expected):
        assert build_oauth_redirect_uri(public_url, callback_path) == expected

    def test_build_oauth_redirect_uri_returns_none_without_public_url(self):
        assert build_oauth_redirect_uri(None, "/oauth/callback") is None
        assert build_oauth_redirect_uri("", "/oauth/callback") is None


class TestOAuthStateSigner:
    def test_extra_round_trips_under_signature(self):
        signer = OAuthStateSigner("secret")
        raw = signer.issue(
            user_keys=("ou_1",),
            scopes=("calendar:calendar",),
            extra={"authorization_id": "az_1"},
        )

        state = signer.consume(raw)

        assert state is not None
        assert state.extra == {"authorization_id": "az_1"}
        assert OAuthStateSigner("other").consume(raw) is None


@pytest.fixture
def authorize_url():
    """Bind ``authorize_url`` on a real region client (default feishu)."""

    def build(region="feishu", **call_kwargs):
        client = FeishuClient("cli_app", "secret", region=region)
        target = call_kwargs.pop("redirect_uri", "https://app.example.com/cb")
        return client.oauth.authorize_url(target, **call_kwargs)

    return build


@pytest.fixture
async def make_oauth_client():
    """Factory for an OAuth client over a mock handler, auto-closed after the test."""
    clients = []

    def build(handler, credential=None):
        transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        if credential is not None:
            client = FeishuClient(credential=credential, transport=transport)
        else:
            client = FeishuClient("cli_app", "secret", transport=transport)
        clients.append(client)
        return client

    yield build
    for client in clients:
        await client.aclose()


class TestAuthorizeUrl:
    def test_endpoint_and_required_fields(self, authorize_url):
        url = authorize_url(redirect_uri="https://app.example.com/callback")
        parts = urlsplit(url)
        assert (parts.scheme, parts.netloc, parts.path) == (
            "https",
            "accounts.feishu.cn",
            "/open-apis/authen/v1/authorize",
        )
        q = _query(url)
        # Contract: OAuth uses client_id (not app_id), response_type=code, the redirect.
        assert q["client_id"] == ["cli_app"]
        assert "app_id" not in q
        assert q["response_type"] == ["code"]
        assert q["redirect_uri"] == ["https://app.example.com/callback"]

    def test_omits_unset_params(self, authorize_url):
        q = _query(authorize_url())
        assert {"prompt", "scope", "state"}.isdisjoint(q)

    @pytest.mark.parametrize(
        "kwargs, expected",
        [
            ({"scope": ["contact:user.id", "contact:user.email"]}, {"scope": ["contact:user.id contact:user.email"]}),
            ({"scope": "contact:user.id"}, {"scope": ["contact:user.id"]}),
            ({"state": "xyz", "prompt": "consent"}, {"state": ["xyz"], "prompt": ["consent"]}),
        ],
    )
    def test_forwards_optional_params(self, authorize_url, kwargs, expected):
        q = _query(authorize_url(**kwargs))
        for key, value in expected.items():
            assert q[key] == value

    def test_lark_region_host(self, authorize_url):
        assert urlsplit(authorize_url(region="lark")).netloc == "accounts.larksuite.com"

    def test_accounts_url_override_wins_over_region(self):
        # An explicit accounts_url overrides the region default (e.g. to correct the unverified lark host).
        client = FeishuClient("cli_app", "secret", region="lark", accounts_url="https://accounts.example.com")
        assert urlsplit(client.oauth.authorize_url("https://app.example.com/cb")).netloc == "accounts.example.com"

    def test_unknown_region_raises_at_call(self):
        # Construction with override succeeds; the error surfaces only when called.
        client = FeishuClient("cli_app", "secret", region="private", base_url="https://feishu.internal.example.com")
        with pytest.raises(ValueError):
            client.oauth.authorize_url("https://app.example.com/cb")

    def test_non_internal_credential_raises(self):
        client = FeishuClient(credential=_NonInternalCredential())
        with pytest.raises(ValueError):
            client.oauth.authorize_url("https://app.example.com/cb")


class TestOAuthProperty:
    def test_property_is_cached(self):
        client = FeishuClient("cli_app", "secret", region="feishu")
        assert client.oauth is client.oauth


class TestExchangeCode:
    async def test_posts_and_returns_tokens(self, make_oauth_client):
        seen = {}
        response = httpx.Response(
            200,
            json={"access_token": "u-acc", "refresh_token": "u-ref", "expires_in": 7200, "token_type": "Bearer"},
        )
        client = make_oauth_client(_recording_handler(seen, response))
        resp = await client.oauth.exchange_code("the-code", redirect_uri="https://app.example.com/cb")

        assert resp["access_token"] == "u-acc"
        assert resp["refresh_token"] == "u-ref"
        assert resp["expires_in"] == 7200
        assert seen["method"] == "POST"
        assert seen["path"].endswith("/open-apis/authen/v2/oauth/token")
        # Contract guard: credentials travel in the body, NOT an Authorization header.
        assert seen["auth"] is None
        body = seen["body"]
        assert body["grant_type"] == "authorization_code"
        assert body["code"] == "the-code"
        assert body["client_id"] == "cli_app"
        assert body["client_secret"] == "secret"
        assert body["redirect_uri"] == "https://app.example.com/cb"

    async def test_omits_redirect_uri_when_absent(self, make_oauth_client):
        seen = {}
        response = httpx.Response(200, json={"access_token": "u-acc", "expires_in": 7200})
        client = make_oauth_client(_recording_handler(seen, response))
        await client.oauth.exchange_code("the-code")
        assert "redirect_uri" not in seen["body"]

    @pytest.mark.parametrize(
        "status, payload, error",
        [
            (400, {"code": 20037, "error": "invalid_grant", "error_description": "code already used"}, FeishuAuthError),
            (400, {"code": 1, "msg": "bad request"}, FeishuApiError),
        ],
    )
    async def test_error_responses_raise(self, make_oauth_client, status, payload, error):
        client = make_oauth_client(lambda request: httpx.Response(status, json=payload))
        with pytest.raises(error) as exc:
            await client.oauth.exchange_code("bad-code")
        if "error_description" in payload:
            assert exc.value.message == payload["error_description"]

    async def test_credential_validated_before_http(self, make_oauth_client):
        # Guard: the credential type is validated before any network call for both flows.
        called = {"n": 0}

        def handler(request):
            called["n"] += 1
            return httpx.Response(200, json={"access_token": "x", "expires_in": 1})

        client = make_oauth_client(handler, credential=_NonInternalCredential())
        with pytest.raises(ValueError):
            await client.oauth.exchange_code("c")
        with pytest.raises(ValueError):
            await client.oauth.refresh("r")
        assert called["n"] == 0


class TestRefresh:
    async def test_body_and_token_rotation(self, make_oauth_client):
        seen = {}
        response = httpx.Response(
            200, json={"access_token": "u-acc2", "refresh_token": "u-ref-NEW", "expires_in": 7200}
        )
        client = make_oauth_client(_recording_handler(seen, response))
        resp = await client.oauth.refresh("u-ref-OLD")
        assert seen["body"]["grant_type"] == "refresh_token"
        assert seen["body"]["refresh_token"] == "u-ref-OLD"
        assert seen["auth"] is None
        # Rotation contract: the new refresh token is returned for the caller to persist.
        assert resp["refresh_token"] == "u-ref-NEW"


class TestUserInfo:
    async def test_sends_user_token_as_bearer(self, make_oauth_client):
        seen = {}
        response = httpx.Response(200, json={"code": 0, "msg": "success", "data": {"open_id": "ou_123", "name": "Ada"}})
        client = make_oauth_client(_recording_handler(seen, response))
        data = await client.oauth.user_info("u-acc-token")
        assert seen["method"] == "GET"
        assert seen["path"].endswith("/open-apis/authen/v1/user_info")
        # Contract guard: the USER token is sent as the bearer, not a tenant token.
        assert seen["auth"] == "Bearer u-acc-token"
        assert data["open_id"] == "ou_123"
        assert data["name"] == "Ada"

    async def test_envelope_reachable_from_result(self, make_oauth_client):
        client = make_oauth_client(
            lambda request: httpx.Response(200, json={"code": 0, "msg": "success", "data": {"open_id": "ou_x"}})
        )
        data = await client.oauth.user_info("u-acc-token")
        assert data.raw_envelope["code"] == 0
        assert data.raw_envelope["msg"] == "success"
