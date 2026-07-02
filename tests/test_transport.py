"""Transport behavior as observed through ``FeishuClient.request``.

These tests drive the public client seam over a fake transport so they describe
what an SDK user actually gets back:

* managed-token injection, URL/auth composition, None-param stripping, and
  business-error surfacing (``TestRequest`` / ``TestErrorSurfacing``);
* transparent 5xx/429 recovery and retry exhaustion contracts, including the
  catchable exception carrying the underlying cause and resource ownership
  (``TestTransparentRecovery`` / ``TestTransientRecovery`` / ``TestExhaustion`` /
  ``TestClientLifecycle``);
* non-enveloped (OAuth token shape) responses, where the body is a bare
  ``{access_token, ...}`` rather than the ``{code, msg, data}`` envelope
  (``TestNonEnvelope*``).
"""

import httpx
import pytest

from feishu import FeishuClient
from feishu._transport import RetryPolicy
from feishu.errors import (
    FeishuApiError,
    FeishuAuthError,
    FeishuError,
    FeishuServerError,
    FeishuTransportError,
)
from tests.conftest import token_handler


async def oauth_request(client):
    """Make an unauthenticated, non-enveloped token-style request."""
    return await client.request("POST", "authen/v2/oauth/token", token_type=None, token=None, expect_envelope=False)


@pytest.fixture
async def make():
    """Build ``FeishuClient``s served by a handler, auto-closing each after the test.

    ``make(handler, retry=..., http_client=...)`` wires ``handler`` behind the
    token-fetch stub. Owned transports are released on teardown.
    """
    created = []

    def _build(handler, *, retry=None, http_client=None, retry_sleep=None):
        def _handler(request):
            return token_handler(request) or handler(request)

        transport = http_client or httpx.AsyncClient(transport=httpx.MockTransport(_handler))
        client = FeishuClient(
            "cli_test",
            "secret",
            retry=retry or RetryPolicy(max_attempts=3, base_delay=0.0, jitter=False),
            transport=transport,
            retry_sleep=retry_sleep,
        )
        created.append(client)
        return client

    yield _build
    for client in created:
        await client.aclose()


class SleepRecorder:
    """Record retry backoff durations without actually sleeping."""

    def __init__(self) -> None:
        self.durations: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.durations.append(delay)


async def no_sleep(_delay: float) -> None:
    return None


def counting(responses):
    """A handler that returns ``responses[min(call_index, last)]`` and tallies calls.

    ``responses`` is a list of zero-arg callables producing an ``httpx.Response``
    (or raising). ``handler.calls`` exposes the number of invocations.
    """

    def handler(request):
        handler.calls += 1
        idx = min(handler.calls - 1, len(responses) - 1)
        return responses[idx]()

    handler.calls = 0
    return handler


def ok(data=None, **extra):
    body = {"code": 0, "msg": "ok"}
    if data is not None:
        body["data"] = data
    body.update(extra)
    return httpx.Response(200, json=body)


def boom():
    raise httpx.ConnectError("boom")


class TestRequest:
    async def test_returns_data_with_managed_bearer(self, make):
        seen = {}

        def handler(request):
            seen["auth"] = request.headers.get("authorization")
            seen["url"] = str(request.url)
            return ok({"x": 1})

        resp = await make(handler).request("GET", "im/v1/messages/abc")
        assert resp["data"]["x"] == 1
        # A tenant token is fetched and forwarded as a Bearer credential.
        assert seen["auth"] == "Bearer t"
        # The path is composed under the Open API prefix on the resolved host.
        assert seen["url"] == "https://open.feishu.cn/open-apis/im/v1/messages/abc"

    async def test_none_params_stripped(self, make):
        seen = {}

        def handler(request):
            seen["params"] = dict(request.url.params)
            return ok({})

        await make(handler).request("GET", "x", params={"keep": "yes", "drop": None})
        # None-valued params never reach the server; set ones do.
        assert "drop" not in seen["params"]
        assert seen["params"]["keep"] == "yes"


class TestErrorSurfacing:
    async def test_business_error_not_retried(self, make):
        handler = counting([lambda: httpx.Response(200, json={"code": 230002, "msg": "denied"})])
        with pytest.raises(FeishuApiError):
            await make(handler).request("GET", "x")
        # Business errors are terminal: the call fails on the first attempt.
        assert handler.calls == 1


class TestTransparentRecovery:
    async def test_transient_5xx_recovers(self, make):
        handler = counting([lambda: httpx.Response(503, json={"code": 0, "msg": "x"}), lambda: ok({"x": 1})])
        resp = await make(handler).request("GET", "x")
        # The caller sees a successful result; the transient failure is invisible.
        assert resp["data"]["x"] == 1
        assert handler.calls > 1  # evidence a retry occurred

    async def test_rate_limit_honors_reset_header(self, make):
        sleeps = SleepRecorder()
        handler = counting(
            [
                lambda: httpx.Response(
                    429, json={"code": 99991400, "msg": "slow"}, headers={"x-ogw-ratelimit-reset": "7"}
                ),
                lambda: ok({"x": 1}),
            ]
        )
        resp = await make(handler, retry_sleep=sleeps).request("GET", "x")
        # Ultimately succeeds after backing off; the wait respects the server's reset hint.
        assert resp["data"]["x"] == 1
        assert sleeps.durations and sleeps.durations[-1] >= 7.0


class TestTransientRecovery:
    async def test_network_error_recovers(self, make):
        handler = counting([boom, lambda: ok({"x": 1})])
        resp = await make(handler).request("GET", "x")
        # The caller gets data back; the dropped connection is invisible.
        assert resp["data"]["x"] == 1
        assert handler.calls > 1  # evidence a retry occurred


class TestExhaustion:
    async def test_network_error_wraps_cause(self, make):
        with pytest.raises(FeishuTransportError) as excinfo:
            await make(lambda r: boom()).request("GET", "x")
        # The underlying httpx error is preserved on ``.original`` for callers to inspect.
        assert isinstance(excinfo.value.original, httpx.RequestError)

    async def test_persistent_5xx_raises(self, make):
        client = make(
            lambda r: httpx.Response(503, json={"code": 0, "msg": "unavailable"}),
            retry=RetryPolicy(max_attempts=2, base_delay=0.0, jitter=False),
        )
        with pytest.raises(FeishuServerError):
            await client.request("GET", "x")

    async def test_non_json_body_raises_with_raw_text(self, make):
        client = make(lambda r: httpx.Response(200, text="not json", headers={"content-type": "text/plain"}))
        with pytest.raises(FeishuError) as excinfo:
            await client.request("GET", "x")
        err = excinfo.value
        # A non-JSON body is surfaced as an error whose raw payload exposes the text.
        assert isinstance(err, FeishuApiError)
        assert err.raw["msg"] == "not json"


class TestClientLifecycle:
    async def test_caller_transport_not_closed(self, make):
        http_client = httpx.AsyncClient(transport=httpx.MockTransport(token_handler))
        client = make(lambda r: httpx.Response(200, json={"code": 0}), http_client=http_client)
        await client.aclose()
        # Ownership stays with the caller: their client remains usable after aclose().
        assert http_client.is_closed is False
        await http_client.aclose()

    async def test_owned_transport_aclose_idempotent(self):
        # No transport supplied -> the client owns it and releases it on aclose().
        client = FeishuClient("cli_test", "secret")
        await client.aclose()
        # Idempotent: closing again does not raise.
        await client.aclose()


class TestNonEnvelopeSuccess:
    async def test_access_token_body_succeeds(self, make):
        client = make(
            lambda r: httpx.Response(200, json={"access_token": "u-1", "refresh_token": "r-1", "expires_in": 7200})
        )
        resp = await oauth_request(client)
        assert resp["access_token"] == "u-1"
        assert resp["refresh_token"] == "r-1"

    async def test_code_zero_body_succeeds(self, make):
        resp = await oauth_request(make(lambda r: httpx.Response(200, json={"code": 0, "msg": "ok"})))
        assert resp["code"] == 0


class TestNonEnvelopeErrors:
    async def test_oauth_error_raises_auth_error(self, make):
        client = make(
            lambda r: httpx.Response(
                400, json={"code": 20037, "error": "invalid_grant", "error_description": "code expired"}
            )
        )
        with pytest.raises(FeishuAuthError) as exc:
            await oauth_request(client)
        # The human-facing reason comes from error_description, surfaced on .message.
        assert exc.value.message == "code expired"

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(200, json={"code": 123, "msg": "boom"}),  # nonzero code, no token
            httpx.Response(404, json={"whatever": True}),  # non-2xx, no token
        ],
        ids=["nonzero-code", "non-2xx"],
    )
    async def test_non_token_failure_raises_api_error(self, make, response):
        with pytest.raises(FeishuApiError):
            await oauth_request(make(lambda r: response))


class TestDownload:
    async def test_returns_raw_bytes(self, make):
        result = await make(lambda r: httpx.Response(200, content=b"PNG_BYTES")).download(
            "im/v1/messages/om_1/resources/file_k1", params={"type": "image"}
        )
        assert result == b"PNG_BYTES"

    async def test_json_error_raises(self, make):
        # A 403 with a JSON error envelope must raise a FeishuError subclass, not return bytes.
        client = make(lambda r: httpx.Response(403, json={"code": 99991663, "msg": "no permission"}))
        with pytest.raises(FeishuError):
            await client.download("im/v1/messages/om_1/resources/file_k1", params={"type": "image"})


class TestUpload:
    async def test_posts_multipart_returns_data(self, make):
        # Transport/client.upload must POST multipart/form-data carrying both the
        # form fields (data=) and the file (files=), and return the envelope's data.
        seen = {}

        def handler(request):
            seen["method"] = request.method
            seen["content_type"] = request.headers.get("content-type", "")
            seen["body"] = request.content
            seen["auth"] = request.headers.get("authorization")
            return ok({"file_token": "fk_1"})

        resp = await make(handler).upload(
            "drive/v1/files/upload_all",
            data={"file_name": "a.txt", "parent_type": "explorer", "parent_node": "fld_1", "size": "3"},
            files={"file": b"abc"},
        )
        # The data envelope is returned, and a managed tenant token was injected.
        assert resp["data"]["file_token"] == "fk_1"
        assert seen["method"] == "POST"
        assert seen["auth"] == "Bearer t"
        # httpx set the multipart boundary itself; the body carries the file + fields.
        assert seen["content_type"].startswith("multipart/form-data")
        body = seen["body"]
        assert b"abc" in body and b'name="file"' in body
        assert b'name="file_name"' in body and b"a.txt" in body
        assert b'name="parent_node"' in body and b"fld_1" in body

    async def test_business_error_raises(self, make):
        client = make(lambda r: httpx.Response(200, json={"code": 230002, "msg": "denied"}))
        with pytest.raises(FeishuApiError):
            await client.upload("drive/v1/files/upload_all", data={"file_name": "a"}, files={"file": b"x"})


class TestNonEnvelopeRecovery:
    async def test_post_5xx_not_retried(self, make):
        # The OAuth token endpoint is a POST (non-idempotent): a 5xx must NOT be retried,
        # since the request may already have been committed server-side.
        handler = counting([lambda: httpx.Response(503, json={"error": "server", "error_description": "down"})])
        with pytest.raises(FeishuServerError):
            await oauth_request(make(handler))
        assert handler.calls == 1  # terminal on the first attempt: no duplicate POST

    async def test_rate_limit_honors_reset_header(self, make):
        sleeps = SleepRecorder()
        handler = counting(
            [
                lambda: httpx.Response(429, json={"error": "rate"}, headers={"x-ogw-ratelimit-reset": "5"}),
                lambda: httpx.Response(200, json={"access_token": "u-1", "expires_in": 7200}),
            ]
        )
        resp = await oauth_request(make(handler, retry_sleep=sleeps))
        assert resp["access_token"] == "u-1"
        assert sleeps.durations and sleeps.durations[-1] >= 5.0


class TestRetryIdempotencyGating:
    """Idempotent methods retry RequestError/5xx; POST/PATCH only retry 429."""

    @pytest.mark.parametrize(
        "method, expected_calls",
        [
            ("GET", 3),  # idempotent: exhausts all attempts on persistent 5xx
            ("PUT", 3),  # idempotent
            ("POST", 1),  # non-idempotent: terminal on first attempt, no duplicate write
            ("PATCH", 1),  # non-idempotent
        ],
    )
    async def test_5xx_retry_by_idempotency(self, make, method, expected_calls):
        handler = counting([lambda: httpx.Response(503, json={"code": 0, "msg": "down"})])
        client = make(handler, retry=RetryPolicy(max_attempts=3, base_delay=0.0, jitter=False))
        with pytest.raises(FeishuServerError):
            await client.request(method, "x", json={"a": 1})
        assert handler.calls == expected_calls

    @pytest.mark.parametrize("method, expected_calls", [("PUT", 2), ("POST", 1)], ids=["put-retries", "post-terminal"])
    async def test_network_retry_by_idempotency(self, make, method, expected_calls):
        # A dropped connection on a POST may have committed: do not resend. PUT is idempotent.
        handler = counting([boom, lambda: ok({"x": 1})])
        client = make(handler, retry=RetryPolicy(max_attempts=3, base_delay=0.0, jitter=False))
        if expected_calls == 1:
            with pytest.raises(FeishuTransportError):
                await client.request(method, "x", json={"a": 1})
        else:
            resp = await client.request(method, "x", json={"a": 1})
            assert resp["data"]["x"] == 1
        assert handler.calls == expected_calls

    async def test_post_429_is_retried(self, make):
        # 429 means the request was rejected (not processed), so retrying a POST is safe.
        handler = counting([lambda: httpx.Response(429, json={"code": 99991400, "msg": "slow"}), lambda: ok({"x": 1})])
        resp = await make(handler).request("POST", "x", json={"a": 1})
        assert resp["data"]["x"] == 1
        assert handler.calls == 2

    async def test_upload_5xx_is_not_retried(self, make):
        # upload() is multipart POST -> non-idempotent: a 5xx must not be resent.
        handler = counting([lambda: httpx.Response(503, json={"code": 0, "msg": "down"})])
        client = make(handler, retry=RetryPolicy(max_attempts=3, base_delay=0.0, jitter=False))
        with pytest.raises(FeishuServerError):
            await client.upload("drive/v1/files/upload_all", data={"file_name": "a"}, files={"file": b"x"})
        assert handler.calls == 1

    async def test_upload_429_is_retried(self, make):
        handler = counting(
            [lambda: httpx.Response(429, json={"code": 99991400, "msg": "slow"}), lambda: ok({"file_token": "fk"})]
        )
        resp = await make(handler).upload("drive/v1/files/upload_all", data={"file_name": "a"}, files={"file": b"x"})
        assert resp["data"]["file_token"] == "fk"
        assert handler.calls == 2

    async def test_download_5xx_is_retried(self, make):
        # download() is a GET -> idempotent: a transient 5xx is retried.
        handler = counting(
            [
                lambda: httpx.Response(503, json={"code": 0, "msg": "down"}),
                lambda: httpx.Response(200, content=b"BYTES"),
            ]
        )
        client = make(handler, retry=RetryPolicy(max_attempts=3, base_delay=0.0, jitter=False))
        result = await client.download("im/v1/messages/om_1/resources/k1", params={"type": "image"})
        assert result == b"BYTES"
        assert handler.calls == 2


class TestResetAfterCap:
    """A server reset hint larger than max_delay is capped, not honored verbatim."""

    @pytest.mark.parametrize(
        "max_delay, reset_after, expected",
        [
            (5.0, 100.0, 5.0),  # hint above cap -> clamped
            (30.0, 7.0, 7.0),  # hint below cap -> honored
        ],
    )
    def test_delay_clamps_reset(self, max_delay, reset_after, expected):
        assert RetryPolicy(max_delay=max_delay, jitter=False).delay(1, reset_after=reset_after) == expected

    async def test_rate_limit_wait_capped(self, make):
        sleeps = SleepRecorder()
        handler = counting(
            [
                lambda: httpx.Response(
                    429, json={"code": 99991400, "msg": "slow"}, headers={"x-ogw-ratelimit-reset": "999"}
                ),
                lambda: ok({"x": 1}),
            ]
        )
        client = make(
            handler,
            retry=RetryPolicy(max_attempts=3, max_delay=5.0, jitter=False),
            retry_sleep=sleeps,
        )
        resp = await client.request("GET", "x")
        assert resp["data"]["x"] == 1
        # The 999s server hint was clamped to max_delay rather than honored verbatim.
        assert sleeps.durations == [5.0]


class TestRetryDeadline:
    """An overall retry-time budget stops a long backoff sequence early."""

    @pytest.mark.parametrize(
        "policy, expected_budget",
        [
            (RetryPolicy(max_delay=30.0, max_attempts=3), 90.0),  # defaults to max_delay * attempts
            (RetryPolicy(max_elapsed=12.0), 12.0),  # explicit max_elapsed wins
        ],
    )
    def test_elapsed_budget(self, policy, expected_budget):
        assert policy.elapsed_budget == expected_budget

    async def test_deadline_stops_retries(self, make):
        # Each backoff would be 10s but the total budget is only 1s, so after the first
        # attempt the projected wait already overruns the budget: no further retries.
        handler = counting([lambda: httpx.Response(503, json={"code": 0, "msg": "down"})])
        client = make(
            handler,
            retry=RetryPolicy(max_attempts=10, base_delay=10.0, max_delay=10.0, jitter=False, max_elapsed=1.0),
        )
        with pytest.raises(FeishuServerError):
            await client.request("GET", "x")
        # Would otherwise have taken up to 10 attempts; the deadline caps it at 1.
        assert handler.calls == 1

    async def test_deadline_allows_retries_within_budget(self, make):
        # Budget comfortably exceeds the planned backoffs, so retries proceed normally.
        handler = counting(
            [
                lambda: httpx.Response(503, json={"code": 0, "msg": "down"}),
                lambda: httpx.Response(503, json={"code": 0, "msg": "down"}),
                lambda: ok({"x": 1}),
            ]
        )
        client = make(
            handler,
            retry=RetryPolicy(max_attempts=5, base_delay=0.1, max_delay=0.1, jitter=False, max_elapsed=100.0),
            retry_sleep=no_sleep,
        )
        resp = await client.request("GET", "x")
        assert resp["data"]["x"] == 1
        assert handler.calls == 3
