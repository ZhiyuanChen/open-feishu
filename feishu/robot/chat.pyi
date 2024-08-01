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

# noqa: E302

from __future__ import annotations

from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from openai import Stream

@overload
def handle_chat(
    request: dict,
    max_num_messages: int | None = ...,
    max_message_length: int | None = ...,
    stop_words: set[str] | None = ...,
    stream: bool = ...,
) -> dict: ...
@overload
def handle_chat(
    request: dict,
    max_num_messages: int | None = ...,
    max_message_length: int | None = ...,
    stream: bool = ...,
) -> dict: ...
def handle_chat_history(
    request: dict,
    max_num_messages: int | None = None,
    max_message_length: int | None = None,
    stop_words: set[str] | None = None,
    stream: bool = True,
) -> dict: ...
def handle_chat_chain(
    request: dict,
    max_num_messages: int | None = None,
    max_message_length: int | None = None,
    stream: bool = True,
) -> dict: ...
