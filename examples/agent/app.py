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

"""可部署的 OpenFeishu Agent 模板。"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Literal, cast

from feishu.agent import Agent, ToolRegistry

BackendName = Literal["ws", "http"]


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name)
    if value:
        return value
    if default is not None:
        return default
    raise RuntimeError(f"{name} is required")


def backend_name() -> BackendName:
    value = env("AGENT_BACKEND", "ws")
    if value not in {"ws", "http"}:
        raise RuntimeError("AGENT_BACKEND must be either 'ws' or 'http'")
    return cast(BackendName, value)


def env_int(name: str, default: int) -> int | str:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        # Preserve malformed text so Agent's pre-construction validator can name the field.
        return value


registry = ToolRegistry()


@registry.register(
    input_schema={
        "type": "object",
        "properties": {
            "tz": {"type": "string", "description": "时区，支持 'utc' 或 'local'，默认 'utc'。"},
        },
        "required": [],
        "additionalProperties": False,
    },
    description="返回当前日期和时间（ISO 8601 字符串）。",
)
def get_time(tz: str = "utc") -> str:
    now = datetime.now(timezone.utc) if tz == "utc" else datetime.now()
    return now.isoformat()


def config() -> dict:
    db_path = env("AGENT_DB_PATH", ".agent/agent.db")
    return {
        "feishu": {
            "app_id": env("FEISHU_APP_ID"),
            "app_secret": env("FEISHU_APP_SECRET"),
            "encrypt_key": os.environ.get("FEISHU_ENCRYPT_KEY") or None,
            "verification_token": os.environ.get("FEISHU_VERIFICATION_TOKEN") or None,
            "region": env("FEISHU_REGION", "feishu"),
        },
        "model": {
            "model": env("OPENAI_MODEL"),
            "api_key": env("OPENAI_API_KEY"),
            "base_url": env("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        },
        "storage": {"path": db_path},
        "oauth": {
            "public_url": os.environ.get("AGENT_PUBLIC_URL") or None,
            "state_secret": os.environ.get("AGENT_OAUTH_STATE_SECRET") or None,
        },
        "server": {
            "host": env("HOST", "0.0.0.0"),
            "port": env_int("PORT", 5654),
            "seen_store": "sqlite",
            "seen_db_path": db_path,
        },
        "reply": {"stream": True},
        "toolkits": [],
        "timezone": env("AGENT_TIMEZONE", "Asia/Shanghai"),
        "runtime": {"shutdown_timeout_seconds": env_int("AGENT_SHUTDOWN_TIMEOUT_SECONDS", 30)},
        "system": env(
            "AGENT_SYSTEM_PROMPT",
            "你是一个简洁的飞书助手。必要时使用工具。",
        ),
    }


def main() -> None:
    Agent(config(), registry=registry).run(backend=backend_name())


if __name__ == "__main__":
    main()
