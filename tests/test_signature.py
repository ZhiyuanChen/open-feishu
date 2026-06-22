"""SignatureVerifier — the public event-webhook signature contract, no HTTP."""

import pytest

from feishu import SignatureVerifier
from feishu.signature import verify_signature
from tests.conftest import sign_event

ENCRYPT_KEY = "ek_secret"
NONCE = "test_nonce"
TS = "1700000000"


def _sign(timestamp, nonce, raw_body, encrypt_key=ENCRYPT_KEY):
    """The webhook MAC (shared scheme), with the verifier's positional ordering."""
    return sign_event(encrypt_key, timestamp, nonce, raw_body)


@pytest.fixture
def verifier():
    """A verifier whose clock matches the signed timestamp (replay window open)."""
    return SignatureVerifier(ENCRYPT_KEY, now=lambda: float(TS))


class TestIsValid:
    def test_valid_mac(self, verifier):
        body = b'{"type": "event"}'
        sig = _sign(TS, NONCE, body)
        assert verifier.is_valid(timestamp=TS, nonce=NONCE, body=body, signature=sig) is True

    def test_tampered_body(self, verifier):
        body = b'{"type": "event"}'
        sig = _sign(TS, NONCE, body)
        assert verifier.is_valid(timestamp=TS, nonce=NONCE, body=b'{"type": "EVIL"}', signature=sig) is False

    @pytest.mark.parametrize("field", ["timestamp", "nonce", "signature"])
    def test_none_field(self, verifier, field):
        body = b"hello"
        kwargs = {"timestamp": TS, "nonce": NONCE, "body": body, "signature": _sign(TS, NONCE, body), field: None}
        assert verifier.is_valid(**kwargs) is False

    def test_non_numeric_timestamp(self):
        body = b"hello"
        verifier = SignatureVerifier(ENCRYPT_KEY, now=lambda: float(TS))
        sig = _sign("nan_ts", NONCE, body)
        assert verifier.is_valid(timestamp="not-a-number", nonce=NONCE, body=body, signature=sig) is False

    @pytest.mark.parametrize(
        "skew, expected",
        [
            (299, True),  # inside the default 300 s window
            (600, False),  # beyond it — stale, rejected
        ],
    )
    def test_replay_window(self, skew, expected):
        body = b'{"type": "event"}'
        sig = _sign(TS, NONCE, body)
        verifier = SignatureVerifier(ENCRYPT_KEY, now=lambda: float(TS) + skew)
        assert verifier.is_valid(timestamp=TS, nonce=NONCE, body=body, signature=sig) is expected

    def test_max_age_none_disables_replay(self):
        body = b'{"type": "event"}'
        sig = _sign(TS, NONCE, body)
        # 9999 s ahead — stale, but the replay check is disabled
        verifier = SignatureVerifier(ENCRYPT_KEY, max_age_seconds=None, now=lambda: float(TS) + 9999)
        assert verifier.is_valid(timestamp=TS, nonce=NONCE, body=body, signature=sig) is True


def _make_headers(ts=TS, nonce=NONCE, sig=None, body=b""):
    """Build a valid signed header dict for is_valid_request."""
    if sig is None:
        sig = _sign(ts, nonce, body)
    return {
        "X-Lark-Signature": sig,
        "X-Lark-Request-Timestamp": ts,
        "X-Lark-Request-Nonce": nonce,
    }


class TestIsValidRequest:
    def test_valid_request(self, verifier):
        body = b'{"schema": "2.0"}'
        assert verifier.is_valid_request(body, _make_headers(body=body)) is True

    def test_tampered_body(self, verifier):
        body = b'{"schema": "2.0"}'
        headers = _make_headers(body=body)
        assert verifier.is_valid_request(b'{"schema": "EVIL"}', headers) is False

    def test_stale_timestamp(self):
        body = b'{"schema": "2.0"}'
        headers = _make_headers(body=body)
        verifier = SignatureVerifier(ENCRYPT_KEY, now=lambda: float(TS) + 600)
        assert verifier.is_valid_request(body, headers) is False

    @pytest.mark.parametrize(
        "drop",
        ["X-Lark-Signature", "X-Lark-Request-Timestamp", "X-Lark-Request-Nonce"],
    )
    def test_missing_header(self, verifier, drop):
        body = b'{"schema": "2.0"}'
        headers = _make_headers(body=body)
        del headers[drop]
        assert verifier.is_valid_request(body, headers) is False

    @pytest.mark.parametrize(
        "headers",
        [
            {"x-lark-signature": "S", "x-lark-request-timestamp": "T", "x-lark-request-nonce": "N"},
            {"X-Lark-SIGNATURE": "S", "x-lark-request-TIMESTAMP": "T", "X-LARK-request-nonce": "N"},
        ],
        ids=["lowercase", "mixed-case"],
    )
    def test_case_insensitive(self, verifier, headers):
        body = b'{"schema": "2.0"}'
        sig = _sign(TS, NONCE, body)
        # fill in the templated values, preserving each test's header casing
        filled = {k: {"S": sig, "T": TS, "N": NONCE}[v] for k, v in headers.items()}
        assert verifier.is_valid_request(body, filled) is True

    def test_non_numeric_timestamp_header(self):
        body = b'{"schema": "2.0"}'
        headers = _make_headers(ts="bad_ts", body=body)
        verifier = SignatureVerifier(ENCRYPT_KEY, now=lambda: float(TS))
        assert verifier.is_valid_request(body, headers) is False


class TestSecurityHardening:
    @pytest.mark.parametrize("empty_field", ["timestamp", "nonce", "signature"])
    def test_rejects_empty_string_fields(self, verifier, empty_field):
        # Gateways may normalize a missing header to "" rather than dropping it;
        # empty timestamp/nonce/signature must fail closed.
        body = b'{"type": "event"}'
        vals = {"timestamp": TS, "nonce": NONCE, empty_field: ""}
        sig = "" if empty_field == "signature" else _sign(vals["timestamp"], vals["nonce"], body)
        assert verifier.is_valid(timestamp=vals["timestamp"], nonce=vals["nonce"], body=body, signature=sig) is False

    def test_request_empty_signature(self, verifier):
        body = b'{"schema": "2.0"}'
        headers = {"X-Lark-Signature": "", "X-Lark-Request-Timestamp": TS, "X-Lark-Request-Nonce": NONCE}
        assert verifier.is_valid_request(body, headers) is False

    def test_empty_encrypt_key_raises(self):
        # A falsy key (e.g. an unset env var) would make the HMAC forgeable.
        with pytest.raises(ValueError):
            SignatureVerifier("")

    def test_max_age_none_verifies_mac(self):
        # The critical invariant: disabling the replay window must NEVER skip the MAC.
        body = b'{"type": "event"}'
        wrong_sig = _sign(TS, NONCE, b"different body")
        v = SignatureVerifier(ENCRYPT_KEY, max_age_seconds=None, now=lambda: float(TS) + 9999)
        assert v.is_valid(timestamp=TS, nonce=NONCE, body=body, signature=wrong_sig) is False


class TestVerifySignature:
    TS = "1700000000"
    NONCE = "abc123"
    KEY = "ek_secret"
    RAW = b'{"encrypt":"payload"}'

    def good(self):
        return sign_event(self.KEY, self.TS, self.NONCE, self.RAW)

    def test_correct_signature_passes(self):
        assert verify_signature(self.TS, self.NONCE, self.KEY, self.RAW, self.good()) is True

    def _tampered_sig(self):
        good = self.good()
        return good[:-1] + ("0" if good[-1] != "0" else "1")

    @pytest.mark.parametrize(
        "ts, nonce, key, raw, sig_attr",
        [
            ("NONCE", "TS", "KEY", "RAW", "good"),  # swapped timestamp/nonce
            ("TS", "NONCE", "other_key", "RAW", "good"),  # wrong key
            ("TS", "NONCE", "KEY", b'{"encrypt":"TAMPERED"}', "good"),  # tampered body
            ("TS", "NONCE", "KEY", "RAW", "_tampered_sig"),  # tampered signature
        ],
        ids=["swapped-ts-nonce", "wrong-key", "tampered-body", "tampered-signature"],
    )
    def test_mismatched_inputs_fail(self, ts, nonce, key, raw, sig_attr):
        resolve = {"TS": self.TS, "NONCE": self.NONCE, "KEY": self.KEY, "RAW": self.RAW}
        ts = resolve.get(ts, ts)
        nonce = resolve.get(nonce, nonce)
        key = resolve.get(key, key)
        raw = resolve.get(raw, raw) if isinstance(raw, str) else raw
        sig = getattr(self, sig_attr)()
        assert verify_signature(ts, nonce, key, raw, sig) is False
