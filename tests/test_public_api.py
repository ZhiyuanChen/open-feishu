"""The package export surface: what each public namespace exposes, and must not.

Consolidates the export-surface coverage for the top-level ``feishu`` package
(``TestPackageSurface``), the ``feishu.streaming`` namespace
(``TestStreamingSurface``), and the ``feishu.agent`` namespace
(``TestAgentSurface``).
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

EXPECTED_EXPORTS = {
    "FeishuClient",
    "RetryPolicy",
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


def _agent_public_modules() -> list[str]:
    agent = importlib.import_module("feishu.agent")
    modules = []
    for module in pkgutil.walk_packages(agent.__path__, prefix=f"{agent.__name__}."):
        parts = module.name.split(".")
        if any(part.startswith("_") for part in parts):
            continue
        modules.append(module.name)
    return sorted(modules)


class TestPackageSurface:
    def test_public_surface_is_exactly_all(self):
        feishu = importlib.import_module("feishu")
        assert set(feishu.__all__) == EXPECTED_EXPORTS
        for name in EXPECTED_EXPORTS:
            assert hasattr(feishu, name), name

    def test_retry_policy_export_is_transport_policy(self):
        feishu = importlib.import_module("feishu")
        transport = importlib.import_module("feishu._transport")

        assert feishu.RetryPolicy is transport.RetryPolicy

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
    CORE_EXPORTS = {"Agent", "Tool", "ToolRegistry", "ToolValidationError", "ToolOutcome", "ToolResult"}

    def test_all_names_are_importable(self):
        agent = importlib.import_module("feishu.agent")
        assert set(agent.__all__) == self.CORE_EXPORTS
        for name in agent.__all__:
            assert hasattr(agent, name), name

    @pytest.mark.parametrize("name", sorted(CORE_EXPORTS))
    def test_core_name_exported(self, name):
        agent = importlib.import_module("feishu.agent")
        assert name in agent.__all__

    @pytest.mark.parametrize(
        "name",
        ["LlmBackend", "register_agent", "PaymentAccount", "PaymentAccountResolver", "list_my_payment_accounts"],
    )
    def test_non_core_agent_names_are_not_reexported(self, name):
        agent = importlib.import_module("feishu.agent")
        assert name not in agent.__all__
        assert not hasattr(agent, name)

    @pytest.mark.parametrize("module_name", _agent_public_modules())
    def test_public_agent_submodules_declare_all(self, module_name):
        module = importlib.import_module(module_name)

        assert hasattr(module, "__all__"), module_name
        assert module.__all__, module_name
        for name in module.__all__:
            assert not name.startswith("_"), f"{module_name} exports private name {name!r}"
            assert hasattr(module, name), f"{module_name}.__all__ contains missing name {name!r}"
