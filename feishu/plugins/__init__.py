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

from feishu.agent.bundles import BUNDLES

from .mlflow import MLflowBundle, MLflowClient
from .ops import OpsBundle
from .slurm import SlurmBundle, SlurmRestdClient

_BUNDLED_PLUGIN_BUNDLES = {
    "mlflow": MLflowBundle,
    "ops": OpsBundle,
    "slurm": SlurmBundle,
}


def register_bundled_plugins() -> tuple[str, ...]:
    r"""把 SDK 内置的第三方插件 bundle 注册进共享 bundle registry。"""
    for name, bundle in _BUNDLED_PLUGIN_BUNDLES.items():
        BUNDLES.register(bundle, name=name, override=True)
    return tuple(_BUNDLED_PLUGIN_BUNDLES)


__all__ = [
    "MLflowBundle",
    "MLflowClient",
    "OpsBundle",
    "SlurmBundle",
    "SlurmRestdClient",
    "register_bundled_plugins",
]
