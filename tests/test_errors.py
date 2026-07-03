"""Error classification and the public exception API.

The mapping from an HTTP status + envelope ``code`` to the exception a caller
catches IS the contract, so it is exercised end-to-end through
``FeishuClient.request`` (what a user actually does: ``except Feishu*Error``
around a call). The exception classes themselves carry public fields and a catch
hierarchy, asserted directly via their public constructors.
"""

import httpx
import pytest

from feishu import FeishuClient
from feishu._transport import RetryPolicy
from feishu.errors import (
    FeishuApiError,
    FeishuAuthError,
    FeishuError,
    FeishuPermissionError,
    FeishuRateLimitError,
    FeishuServerError,
    FeishuTransportError,
)
from tests.conftest import token_handler


@pytest.fixture
async def client_returning():
    """Factory for a ``FeishuClient`` whose next non-token request returns
    ``status``/``payload``; built clients are closed on teardown."""
    clients = []

    def _make(status, payload, *, headers=None):
        def _handler(request):
            return token_handler(request) or httpx.Response(status, json=payload, headers=headers or {})

        # max_attempts=1 so 5xx/429 surface immediately instead of retrying.
        client = FeishuClient(
            "cli_test",
            "secret",
            retry=RetryPolicy(max_attempts=1),
            transport=httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
        )
        clients.append(client)
        return client

    yield _make
    for client in clients:
        await client.aclose()


class TestErrorMapping:
    """An HTTP status + envelope code maps to the exception a caller catches."""

    @pytest.mark.parametrize(
        ("status", "payload", "expected"),
        [
            # HTTP 429 -> rate limit, regardless of the envelope code.
            (429, {"code": 99991400, "msg": "slow"}, FeishuRateLimitError),
            # HTTP 5xx -> retriable server error.
            (503, {"code": 0, "msg": "unavailable"}, FeishuServerError),
            # Auth codes on an otherwise-OK HTTP 200 -> auth error.
            (200, {"code": 99991663, "msg": "bad token"}, FeishuAuthError),
            (200, {"code": 99991668, "msg": "expired"}, FeishuAuthError),
            # Permission code -> permission error (a subclass of auth error).
            (200, {"code": 99991672, "msg": "no scope"}, FeishuPermissionError),
            # OAuth invalid_grant shape on a 4xx -> auth error.
            (400, {"code": 20037, "error": "invalid_grant"}, FeishuAuthError),
            # Not every 99991xxx code is auth (e.g. invalid user type) -> generic API error.
            (200, {"code": 99991674, "msg": "bad user type"}, FeishuApiError),
            # A plain business error -> generic API error.
            (200, {"code": 230002, "msg": "denied"}, FeishuApiError),
        ],
    )
    async def test_status_and_code_map_to_exception(self, client_returning, status, payload, expected):
        client = client_returning(status, payload)
        with pytest.raises(expected):
            await client.request("GET", "x")

    async def test_rate_limit_exposes_reset_after(self, client_returning):
        client = client_returning(429, {"code": 99991400, "msg": "slow"}, headers={"x-ogw-ratelimit-reset": "2"})
        with pytest.raises(FeishuRateLimitError) as exc:
            await client.request("GET", "x")
        assert exc.value.reset_after == 2.0

    async def test_permission_is_catchable_as_auth(self, client_returning):
        # A caller writing `except FeishuAuthError` also catches permission failures.
        client = client_returning(200, {"code": 99991672, "msg": "no scope"})
        with pytest.raises(FeishuAuthError):
            await client.request("GET", "x")


class TestExceptionAPI:
    """The public exception classes carry fields and a stable catch hierarchy."""

    def test_base_error_fields(self):
        e = FeishuError(99991663, "bad", log_id="lg-1", raw={"x": 1})
        assert e.code == 99991663
        assert e.message == "bad"
        assert e.log_id == "lg-1"
        assert e.raw == {"x": 1}
        assert "99991663" in str(e) and "lg-1" in str(e)

    def test_signature_and_crypto_subclass_base(self):
        from feishu.errors import FeishuCryptoError, FeishuSignatureError

        sig = FeishuSignatureError(401, "signature mismatch", log_id="lg-2")
        assert isinstance(sig, FeishuError)
        assert sig.code == 401 and sig.message == "signature mismatch" and sig.log_id == "lg-2"

        crypto = FeishuCryptoError(-1, "bad padding", raw={"encrypt": "x"})
        assert isinstance(crypto, FeishuError)
        assert crypto.code == -1 and crypto.raw == {"encrypt": "x"}
        assert "bad padding" in str(crypto)


class TestExceptionExports:
    """The catchable exception types are reachable from the package surface.

    Keep these assertions next to the error hierarchy they cover.
    """

    def test_signature_and_crypto_exported(self):
        import feishu

        assert hasattr(feishu, "FeishuSignatureError")
        assert hasattr(feishu, "FeishuCryptoError")

    def test_permission_exported_as_auth_subclass(self):
        import feishu

        assert hasattr(feishu, "FeishuPermissionError")
        assert issubclass(feishu.FeishuPermissionError, feishu.FeishuAuthError)


class TestTransportError:
    """``FeishuTransportError`` wraps the underlying cause and is a ``FeishuError``."""

    def test_wraps_original(self):
        cause = ValueError("x")
        e = FeishuTransportError("request failed: boom", original=cause)
        assert e.code == -1
        assert e.original is cause
        assert isinstance(e, FeishuError)
        assert "request failed" in str(e)

    def test_original_defaults_to_none(self):
        e = FeishuTransportError("request failed: boom")
        assert e.original is None
        assert e.code == -1
        assert e.message == "request failed: boom"
