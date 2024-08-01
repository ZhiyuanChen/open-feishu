# open-feishu
# Copyright (C) 2024-Present  Zhiyuan Chen

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

r"""
系统变量

Attributes: 飞书:
    APP_ID: 飞书应用 ID。默认读取环境变量的 `APP_ID`。
        如果在调用函数时未提供 `app_id` 参数，则使用此值。
    APP_SECRET: 飞书应用密钥。默认读取环境变量的 `APP_SECRET`。
        如果在调用函数时未提供 `app_secret` 参数，则使用此值。
    VERIFICATION_TOKEN: 飞书验证令牌。默认读取环境变量的 `VERIFICATION_TOKEN`。
    ENCRYPT_KEY: 飞书加密密钥。默认读取环境变量的 `ENCRYPT_KEY`。
    BASE_URL: 飞书 API 基础 URL。默认为 `"https://open.feishu.cn/open-apis/"`。

Attributes: 请求:
    ACCESS_TOKEN_REFRESH_OFFSET: 飞书访问刷新偏移量。默认为 1800 秒。
    MAX_RETRIES: 飞书请求最大重试次数。默认为 5 次。
    BACKOFF_FACTOR: 飞书请求指数退避因子。默认为 4。
    STREAMING_STATUS_TEXT: 飞书流状态文本。默认为 `"生成中..."`。

Attributes: 机器人:
    MAX_NUM_MESSAGES: 机器人回复消息时读取的最大历史消息数量。默认为 200 条。
    MAX_MESSAGE_LENGTH: 机器人回复消息时读取的最大历史消息长度。默认为 65535 字。
    STOP_WORDS: 机器人回复消息时，遇到这些关键词将停止读取历史消息。默认为 `{"/clear"}`。

Attributes: OpenAI:
    OPENAI_API_KEY: OpenAI API 密钥。默认读取环境变量的 `OPENAI_API_KEY`。
    OPENAI_BASE_URL: OpenAI API URL。默认读取环境变量的 `OPENAI_BASE_URL`。
    OPENAI_MODEL: OpenAI API 模型。默认为 `"gpt-4o-mini"`。
    SYSTEM_PROMPT: OpenAI API 系统提示。
    SYSTEM_PROMPT_FILE: OpenAI API 系统提示文件，应当是 TEXT 格式。
        只在 `SYSTEM_PROMPT` 为空时使用。
    SYSTEM_INFO: OpenAI API 系统信息，用于存储系统变量。
        值将在构建 System Prompt 时被注入其中。
    SYSTEM_INFO_FILE: OpenAI API 系统信息文件，应当是 YAML 格式。
        只在 `SYSTEM_INFO` 为空时使用。
"""

from __future__ import annotations

from os import getenv
from typing import Any, Dict

APP_ID: str | None = getenv("APP_ID")
APP_SECRET: str | None = getenv("APP_SECRET")
VERIFICATION_TOKEN: str | None = getenv("VERIFICATION_TOKEN")
ENCRYPT_KEY: str | None = getenv("ENCRYPT_KEY")
BASE_URL = "https://open.feishu.cn/open-apis/"

ACCESS_TOKEN_REFRESH_OFFSET = 1800
MAX_RETRIES = 5
BACKOFF_FACTOR = 4
OPEN_ID: str | None = getenv("OPEN_ID")
UNION_ID: str | None = getenv("UNION_ID")

OPENAI_API_KEY: str | None = getenv("OPENAI_API_KEY")
OPENAI_BASE_URL: str | None = getenv("OPENAI_BASE_URL")
OPENAI_MODEL: str = getenv("OPENAI_MODEL", "gpt-4o-mini")
SYSTEM_PROMPT: str
SYSTEM_PROMPT_FILE: str | None = getenv("SYSTEM_PROMPT_FILE")
SYSTEM_INFO: Dict[str, Any]
SYSTEM_INFO_FILE: str | None = getenv("SYSTEM_INFO_FILE")

STREAMING_STATUS_TEXT = "生成中..."
MAX_NUM_MESSAGES = 200
MAX_MESSAGE_LENGTH = 65535
STOP_WORDS = {"/clear"}
