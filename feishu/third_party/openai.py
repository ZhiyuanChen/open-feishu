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

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytz
from chanfig import FlatDict
from lazy_imports import try_import

with try_import() as openai:
    from openai import OpenAI, Stream
    from openai.types.chat import ChatCompletion

from feishu import variables
from feishu._version import __version__

DEFAULT_SYSTEM_PROMPT = """You are a chat bot provided by OpenFeishu.

System Info:
Version: {version}
Current Time: {time:%Y-%m-%d %H:%M:%S %Z}
"""


def get_gpt_completions(
    messages: list[dict[str, str]],
    stream: bool = True,
    system_prompt: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    **kwargs,
) -> Stream | ChatCompletion:
    r"""
    获取 OpenAI 的聊天补全。

    如果消息列表的第一个消息的 `role` 不是 `"system"`，则会在消息列表的开头插入系统提示。

    Args:
        messages: 消息列表。
        stream: 是否使用流式 API。默认为 ``True``。
        system_prompt: 系统提示。
        api_key: OpenAI API 密钥。
            默认读取[feishu.variables][]中的 `OPENAI_API_KEY`。
        base_url: OpenAI API 地址。
            默认读取[feishu.variables][]中的 `OPENAI_BASE_URL`。
        model: OpenAI API 模型。
            默认读取[feishu.variables][]中的 `OPENAI_MODEL`。
        kwargs: 系统信息。
    """
    openai.check()
    client = OpenAI(
        api_key=api_key or variables.OPENAI_API_KEY,
        base_url=base_url or variables.OPENAI_BASE_URL,
    )
    model = model or variables.OPENAI_MODEL

    if messages[0].get("role") != "system":
        prompt = get_system_prompt(system_prompt, model=model, **kwargs)
        messages.insert(0, {"role": "system", "content": prompt})

    return client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        stream=stream,
    )


def get_system_prompt(system_prompt: str | None = None, **kwargs) -> str:
    r"""
    构造 OpenAI API 的系统提示。

    Args:
        system_prompt: 系统提示。
            默认读取[feishu.variables][]中的 `SYSTEM_PROMPT`。
            如果为空，则使用[feishu.variables][]中的 `SYSTEM_PROMPT_FILE`。
            如果仍为空，则返回默认值。
        kwargs: 系统信息。
            系统信息将被注入到系统提示中。
    """
    if system_prompt is None:
        if hasattr(variables, "SYSTEM_PROMPT") and variables.SYSTEM_PROMPT:
            system_prompt = variables.SYSTEM_PROMPT
        elif hasattr(variables, "SYSTEM_PROMPT_FILE") and variables.SYSTEM_PROMPT_FILE:
            with open(variables.SYSTEM_PROMPT_FILE, encoding="utf8") as file:
                system_prompt = file.read()
    if system_prompt is None:
        return "You are a chat bot provided by OpenFeishu."
    return system_prompt.format(**get_system_info(**kwargs))


def get_system_info(**kwargs) -> dict[str, Any]:
    r"""
    系统信息。

    Args:
        kwargs: 系统信息。
    """
    system_info: dict[str, Any] = {}
    if hasattr(variables, "SYSTEM_INFO") and variables.SYSTEM_INFO:
        system_info = variables.SYSTEM_INFO
    elif hasattr(variables, "SYSTEM_INFO_FILE") and variables.SYSTEM_INFO_FILE:
        system_info = FlatDict(variables.SYSTEM_INFO_FILE)
    tz = pytz.timezone(system_info.get("SYSTEM_LOCALE", "Asia/Hong_Kong"))
    system_info["time"] = datetime.now(timezone.utc).astimezone(tz)
    system_info["timezone"] = tz.zone
    system_info["package"] = "OpenFeishu"
    system_info["version"] = __version__
    if kwargs:
        system_info.update(kwargs)
    return system_info
