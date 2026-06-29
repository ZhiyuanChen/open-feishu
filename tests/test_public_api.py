"""The package export surface: what each public namespace exposes, and must not.

Consolidates the export-surface coverage for the top-level ``feishu`` package
(``TestPackageSurface``), the ``feishu.streaming`` namespace
(``TestStreamingSurface``), and the ``feishu.agent`` namespace
(``TestAgentSurface``).
"""

from __future__ import annotations

import importlib

import pytest

EXPECTED_EXPORTS = {
    "FeishuClient",
    "FeishuError",
    "FeishuApiError",
    "FeishuRateLimitError",
    "FeishuAuthError",
    "FeishuPermissionError",
    "FeishuServerError",
    "FeishuTransportError",
    "FeishuSignatureError",
    "FeishuCryptoError",
    "SignatureVerifier",
    "verify_signature",
    "is_permission_error",
    "permission_subjects",
    "install_redaction",
}


class TestPackageSurface:
    def test_public_surface_is_exactly_all(self):
        feishu = importlib.import_module("feishu")
        assert set(feishu.__all__) == EXPECTED_EXPORTS
        for name in EXPECTED_EXPORTS:
            assert hasattr(feishu, name), name

    @pytest.mark.parametrize("gone", ["send_message", "get_tenant_access_token", "variables", "handle_chat"])
    def test_legacy_attr_gone(self, gone):
        feishu = importlib.import_module("feishu")
        assert not hasattr(feishu, gone)

    @pytest.mark.parametrize("mod", ["feishu.variables", "feishu.utils"])
    def test_legacy_module_gone(self, mod):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod)


class TestStreamingSurface:
    def test_exports_stream_card(self):
        import feishu.streaming as s

        assert "stream_card" in s.__all__
        assert hasattr(s, "stream_card")


class TestAgentSurface:
    def test_all_names_are_importable(self):
        agent = importlib.import_module("feishu.agent")
        for name in agent.__all__:
            assert hasattr(agent, name), name

    @pytest.mark.parametrize("name", ["Agent", "LlmBackend", "ToolRegistry", "register_agent"])
    def test_core_name_exported(self, name):
        agent = importlib.import_module("feishu.agent")
        assert name in agent.__all__
