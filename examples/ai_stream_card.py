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

"""流式 LLM 响应直推飞书消息卡片示例。

本示例演示如何用 ``stream_text`` 将大模型的流式输出实时写入飞书 CardKit 消息卡片，
并以一个独立的 ``async main`` 运行。

``stream_text`` 适配 OpenAI / Anthropic 两种提供商的流，并将文本增量 token 逐一
传给 ``client.stream_card``，后者使用 CardKit 协议实时刷新卡片内容。

用法
----

**OpenAI**::

    pip install 'open-feishu[openai]'
    FEISHU_APP_ID=... FEISHU_APP_SECRET=... OPENAI_API_KEY=... RECEIVE_ID=... python examples/ai_stream_card.py

**Anthropic**::

    pip install 'open-feishu[anthropic]'
    FEISHU_APP_ID=... FEISHU_APP_SECRET=... ANTHROPIC_API_KEY=... RECEIVE_ID=... python examples/ai_stream_card.py

环境变量
--------
    FEISHU_APP_ID, FEISHU_APP_SECRET  飞书应用凭证（由 FeishuClient 自动读取）。
    OPENAI_API_KEY                    OpenAI API 密钥（使用 OpenAI 后端时需要）。
    ANTHROPIC_API_KEY                 Anthropic API 密钥（使用 Anthropic 后端时需要）。
    RECEIVE_ID                        消息接收方的 open_id / chat_id。
    RECEIVE_ID_TYPE                   接收方 ID 类型，默认 ``open_id``。
    PROVIDER                          ``openai``（默认）或 ``anthropic``。
"""

from __future__ import annotations

import asyncio
import os

from feishu import FeishuClient
from feishu.agent.streaming import stream_text


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


async def main() -> None:
    # Sending a real card requires an explicit target; do not hide that behind a fake default.
    receive_id: str = required_env("RECEIVE_ID")
    receive_id_type: str = os.environ.get("RECEIVE_ID_TYPE", "open_id")
    prompt = "请用三句话介绍飞书开放平台。"

    provider = os.environ.get("PROVIDER", "openai").lower()

    if provider == "anthropic":
        # ----------------------------------------------------------------
        # Anthropic: client.messages.stream() returns an async context manager.
        # stream_text detects __aenter__ and enters it automatically.
        # ----------------------------------------------------------------
        import anthropic  # pip install 'open-feishu[anthropic]'

        ac = anthropic.AsyncAnthropic()
        provider_stream = ac.messages.stream(
            model="claude-3-5-haiku-latest",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
    else:
        # ----------------------------------------------------------------
        # OpenAI: client.chat.completions.create(stream=True) returns an
        # async iterator of ChatCompletionChunk objects.
        # stream_text detects the openai module and routes accordingly.
        # ----------------------------------------------------------------
        import openai  # pip install 'open-feishu[openai]'

        oc = openai.AsyncOpenAI()
        provider_stream = await oc.chat.completions.create(
            model="gpt-4o-mini",
            stream=True,
            messages=[{"role": "user", "content": prompt}],
        )

    # FeishuClient reads FEISHU_APP_ID / FEISHU_APP_SECRET from the environment.
    async with FeishuClient() as client:
        # stream_card consumes the AsyncIterator[str] produced by stream_text and
        # progressively updates a live CardKit card in the Feishu conversation.
        card_id = await client.stream_card(
            stream_text(provider_stream),
            receive_id=receive_id,
            receive_id_type=receive_id_type,
        )
    print(f"Streamed card sent, card_id={card_id}")


if __name__ == "__main__":
    asyncio.run(main())
