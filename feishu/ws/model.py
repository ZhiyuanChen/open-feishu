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

from dataclasses import dataclass
from typing import Any


@dataclass
class ClientConfig:
    r"""
    握手时由服务端下发的长连接客户端参数。

    握手响应的 `data.ClientConfig` 使用 PascalCase 键，本类将其映射为蛇形命名字段，
    缺失字段保留默认值。`ping_interval` 还会在每次收到心跳回复（pong）携带新的
    `ClientConfig` 时被刷新。

    Args:
        reconnect_count: 允许的重连次数；`-1` 表示无限重连。
        reconnect_interval: 两次重连之间的等待秒数。
        reconnect_nonce: 重连随机抖动窗口（秒），用于打散并发重连。
        ping_interval: 发送心跳（ping）控制帧的间隔秒数。

    飞书文档:
        [事件概述](https://open.feishu.cn/document/server-docs/event-subscription-guide/overview)

    Examples:
        >>> ClientConfig().reconnect_count
        -1
        >>> ClientConfig(ping_interval=60.0).ping_interval
        60.0
    """

    reconnect_count: int = -1
    reconnect_interval: float = 120.0
    reconnect_nonce: float = 30.0
    ping_interval: float = 120.0


def client_config_from_dict(data: dict[str, Any]) -> ClientConfig:
    r"""
    将握手响应中的 `ClientConfig`（PascalCase 键）映射为 [ClientConfig][feishu.ws.model.ClientConfig]。

    仅当对应键存在且值不为 `None` 时才覆盖默认值，因此服务端可只下发部分字段。

    Args:
        data: 握手响应 `data.ClientConfig` 字典，可能为空。

    Returns:
        映射后的客户端配置。

    Examples:
        >>> cfg = client_config_from_dict({"ReconnectCount": 5, "PingInterval": 60})
        >>> cfg.reconnect_count, cfg.ping_interval
        (5, 60.0)
        >>> client_config_from_dict({}).reconnect_count
        -1
        >>> client_config_from_dict({"ReconnectInterval": None}).reconnect_interval
        120.0
    """
    cfg = ClientConfig()
    if data.get("ReconnectCount") is not None:
        cfg.reconnect_count = int(data["ReconnectCount"])
    if data.get("ReconnectInterval") is not None:
        cfg.reconnect_interval = float(data["ReconnectInterval"])
    if data.get("ReconnectNonce") is not None:
        cfg.reconnect_nonce = float(data["ReconnectNonce"])
    if data.get("PingInterval") is not None:
        cfg.ping_interval = float(data["PingInterval"])
    return cfg
