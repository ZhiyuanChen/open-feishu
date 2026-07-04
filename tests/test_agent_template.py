# OpenFeishu
# Copyright (C) 2024-Present  DanLing

# This file is part of OpenFeishu.

# OpenFeishu is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.

# OpenFeishu is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# For additional terms and clarifications, please refer to our License FAQ at:
# <https://multimolecule.danling.org/about/license-faq>.

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "examples" / "agent"


def load_template_module():
    module_path = TEMPLATE / "app.py"
    spec = importlib.util.spec_from_file_location("agent_example_app", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_agent_template_has_deploy_files() -> None:
    expected = {
        "README.md",
        "app.py",
        ".env.example",
        "requirements.txt",
        "Dockerfile",
        "compose.yml",
    }

    assert expected <= {path.name for path in TEMPLATE.iterdir()}


def test_agent_template_import_is_side_effect_free() -> None:
    module = load_template_module()

    assert callable(module.config)
    assert callable(module.main)
    assert hasattr(module, "registry")


def test_agent_template_documents_env_surface() -> None:
    env = (TEMPLATE / ".env.example").read_text()
    readme = (TEMPLATE / "README.md").read_text()
    for name in (
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_ENCRYPT_KEY",
        "FEISHU_VERIFICATION_TOKEN",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "AGENT_BACKEND",
        "AGENT_DB_PATH",
        "AGENT_TIMEZONE",
        "HOST",
        "PORT",
    ):
        assert f"{name}=" in env
        assert name in readme


def test_agent_template_fails_fast_on_missing_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)

    module = load_template_module()

    with pytest.raises(RuntimeError, match="FEISHU_APP_ID is required"):
        module.config()
