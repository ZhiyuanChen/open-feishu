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

from .mlflow import MLflowBundle, MLflowClient
from .ops import OpsBundle
from .registry import GatewayIntegration, IntegrationRegistry
from .slurm import SlurmBundle, SlurmRestdClient, SlurmWebGatewayClient

INTEGRATIONS = IntegrationRegistry()

_BUNDLED_TOOL_INTEGRATIONS = {
    "mlflow": MLflowBundle,
    "ops": OpsBundle,
    "slurm": SlurmBundle,
}


def register_bundled_integrations() -> tuple[str, ...]:
    r"""Register the SDK's built-in integrations explicitly."""
    for name, bundle in _BUNDLED_TOOL_INTEGRATIONS.items():
        INTEGRATIONS.register_tool_bundle(name, bundle, override=True)
    return INTEGRATIONS.tool_bundle_names


__all__ = [
    "GatewayIntegration",
    "INTEGRATIONS",
    "IntegrationRegistry",
    "MLflowBundle",
    "MLflowClient",
    "OpsBundle",
    "SlurmBundle",
    "SlurmRestdClient",
    "SlurmWebGatewayClient",
    "register_bundled_integrations",
]
