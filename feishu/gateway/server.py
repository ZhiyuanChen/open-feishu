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

from collections.abc import Callable, Mapping
from typing import Any

from starlette.applications import Starlette

from .app import GatewayConfigure, create_gateway
from .config import GatewayConfig

GatewayRunner = Callable[..., Any]


def create_app_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    configure: GatewayConfigure | None = None,
    client: Any | None = None,
) -> Starlette:
    r"""从环境变量创建通用飞书网关应用。"""
    return create_gateway(GatewayConfig.from_env(environ), configure=configure, client=client)


def run_gateway(
    environ: Mapping[str, str] | None = None,
    *,
    configure: GatewayConfigure | None = None,
    client: Any | None = None,
    runner: GatewayRunner | None = None,
) -> None:
    r"""使用 uvicorn 运行通用飞书网关。"""
    config = GatewayConfig.from_env(environ)
    app = create_gateway(config, configure=configure, client=client)
    if runner is None:
        import uvicorn

        runner = uvicorn.run
    runner(app, host=config.host, port=config.port)


def main() -> None:
    r"""``feishu-gateway`` 的命令行入口。"""
    run_gateway()


if __name__ == "__main__":
    main()
