"""Secret redaction observed through the public logging API.

Every test logs through a ``logging.Logger`` (as a real caller does) and inspects
the rendered emitted message — the user-facing contract is "secrets must never
reach a handler", not how the filter mutates a LogRecord.
"""

import logging

import pytest

from feishu._logging import install_redaction

_REDACTED = "***REDACTED***"


class _Capture(logging.Handler):
    """Captures the fully rendered message (``msg % args``) each record produces."""

    def __init__(self):
        super().__init__()
        self.records: list[str] = []

    def emit(self, record):
        self.records.append(record.getMessage())

    @property
    def last(self) -> str:
        return self.records[-1]


@pytest.fixture
def make_logger(request):
    """Factory for a redaction-installed logger + capture handler, isolated per test.

    Returns ``(logger, capture)``; accepts the same ``secrets`` install_redaction does.
    Handlers are torn down for every logger built.
    """
    built: list[tuple[logging.Logger, _Capture]] = []

    def _make(*, secrets=()):
        name = f"feishu.test.{request.node.name}.{len(built)}"
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        install_redaction(name, secrets=secrets)
        cap = _Capture()
        logger.addHandler(cap)
        built.append((logger, cap))
        return logger, cap

    yield _make
    for logger, cap in built:
        logger.removeHandler(cap)


class TestPatternRedaction:
    """Secret values matched by the built-in patterns are scrubbed; keys stay readable."""

    @pytest.mark.parametrize(
        "msg, leaked",
        [
            pytest.param("Authorization: Bearer abc.def-123_XYZ", "abc.def-123_XYZ", id="bearer"),
            pytest.param('{"app_secret": "s3cret-value"}', "s3cret-value", id="app_secret"),
            pytest.param('{"tenant_access_token": "t-tok-987"}', "t-tok-987", id="tenant_access_token"),
            pytest.param('{"encrypt_key": "ek-abc123"}', "ek-abc123", id="encrypt_key"),
            # Base64-style values (+, /, =) must not leak any tail.
            pytest.param('"tenant_access_token": "t-AB+cd/ef=="', "+cd/ef==", id="base64_tail"),
            # JSON and form-encoded client_secret shapes.
            pytest.param('{"client_secret": "wmbv-secret-abc123"}', "wmbv-secret-abc123", id="client_secret_json"),
            pytest.param(
                "client_secret=wmbv-secret-abc123&grant_type=authorization_code",
                "wmbv-secret-abc123",
                id="client_secret_form",
            ),
        ],
    )
    def test_secret_value_scrubbed(self, make_logger, msg, leaked):
        logger, cap = make_logger()
        logger.debug(msg)
        assert leaked not in cap.last
        assert _REDACTED in cap.last

    @pytest.mark.parametrize("key", ["client_secret", "tenant_access_token", "app_secret", "encrypt_key"])
    def test_key_stays_readable(self, make_logger, key):
        # Only the value is redacted; the surrounding key remains for diagnosability.
        logger, cap = make_logger()
        logger.debug(f'{{"{key}": "secret-value-xyz"}}')
        assert key in cap.last
        assert "secret-value-xyz" not in cap.last


class TestExtraLiteralSecrets:
    """Caller-supplied literal secrets are scrubbed wherever they appear."""

    def test_literal_secret_scrubbed(self, make_logger):
        logger, cap = make_logger(secrets=["card-token-xyz"])
        logger.info("updating card with token card-token-xyz")
        assert "card-token-xyz" not in cap.last
        assert _REDACTED in cap.last

    def test_literal_secret_in_dict_args(self, make_logger):
        # The %(name)s dict-args logging shape must still redact secret values.
        logger, cap = make_logger(secrets=["t-tok-987"])
        logger.info("token=%(token)s", {"token": "t-tok-987"})
        assert "t-tok-987" not in cap.last
        assert _REDACTED in cap.last


class TestArgHandling:
    def test_numeric_args_preserved(self, make_logger):
        # Non-str args must pass through unchanged so %-formatting (e.g. %d) still
        # works, while str args are scrubbed. Coercing 5 -> "5" would break "%d"
        # at emit with a TypeError.
        logger, cap = make_logger()
        logger.info("n=%d t=%s", 5, "Bearer tok-secret-abc123")
        assert cap.last.startswith("n=5 ")  # %d rendered the int unchanged, no TypeError
        assert "tok-secret-abc123" not in cap.last
        assert _REDACTED in cap.last


class TestIdempotency:
    def test_double_install_redacts_once(self, make_logger):
        # Guard: a double install must not double-redact a secret.
        name = "feishu.test.idempotent"
        first = install_redaction(name)
        second = install_redaction(name)
        assert first is second  # the existing filter is reused, not re-added
        logger, cap = make_logger()  # reuse fixture only for teardown of its own handler
        # Drive the doubly-installed logger directly.
        idem_logger = logging.getLogger(name)
        idem_logger.setLevel(logging.DEBUG)
        idem_logger.addHandler(cap)
        try:
            idem_logger.debug('{"app_secret": "s3cret-value"}')
        finally:
            idem_logger.removeHandler(cap)
        assert "s3cret-value" not in cap.last
        assert cap.last.count(_REDACTED) == 1  # one marker, no double-artifact

    def test_double_install_merges_literal_secrets(self, make_logger):
        name = "feishu.test.idempotent.merge"
        first = install_redaction(name)
        second = install_redaction(name, secrets=["card-token-xyz"])
        assert first is second
        logger, cap = make_logger()
        idem_logger = logging.getLogger(name)
        idem_logger.setLevel(logging.DEBUG)
        idem_logger.addHandler(cap)
        try:
            idem_logger.info("token=card-token-xyz")
        finally:
            idem_logger.removeHandler(cap)
        assert "card-token-xyz" not in cap.last
        assert _REDACTED in cap.last
