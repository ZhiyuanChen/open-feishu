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
def send_message(
    message: str,
    receive_id: str | None = None,
    receive_id_type: str | None = None,
    uuid: str = "",
    **kwargs,
): ...
@overload
def send_message(
    message: dict,
    receive_id: str | None = None,
    receive_id_type: str | None = None,
    uuid: str = "",
    **kwargs,
): ...
@overload
def send_message(
    message: Stream,
    receive_id: str,
    receive_id_type: str | None = None,
    uuid: str = "",
    **kwargs,
): ...
@overload
def send_message(
    message: str,
    message_id: str,
    **kwargs,
): ...
@overload
def send_message(
    message: dict,
    message_id: str,
    **kwargs,
): ...
@overload
def send_message(
    message: Stream,
    message_id: str,
    **kwargs,
): ...
@overload
def send_message_content(
    content: str,
    receive_id: str | None = None,
    receive_id_type: str | None = None,
    uuid: str = "",
    **kwargs,
): ...
@overload
def send_message_content(
    content: dict,
    receive_id: str | None = None,
    receive_id_type: str | None = None,
    uuid: str = "",
    **kwargs,
): ...
def send_message_stream(
    stream: Stream,
    receive_id: str,
    receive_id_type: str | None = None,
    uuid: str = "",
    **kwargs,
): ...
@overload
def reply_message(
    message: str,
    message_id: str,
    message_type: str | None = None,
    reply_in_thread: bool | None = None,
    uuid: str | None = None,
    **kwargs,
): ...
@overload
def reply_message(
    message: dict,
    message_id: str,
    message_type: str | None = None,
    reply_in_thread: bool | None = None,
    uuid: str | None = None,
    **kwargs,
): ...
@overload
def reply_message(
    message: Stream,
    message_id: str,
    uuid: str | None = None,
    **kwargs,
): ...
@overload
def reply_message_content(
    message: str,
    message_id: str,
    message_type: str | None = None,
    reply_in_thread: bool | None = None,
    uuid: str | None = None,
    **kwargs,
): ...
@overload
def reply_message_content(
    message: dict,
    message_id: str,
    message_type: str | None = None,
    reply_in_thread: bool | None = None,
    uuid: str | None = None,
    **kwargs,
): ...
def reply_message_stream(stream: Stream, message_id: str, uuid: str = "", **kwargs): ...
def update_message(message: str | dict, message_id: str, **kwargs): ...
def patch_message(message: str | dict, message_id: str, **kwargs): ...
def recall_message(message_id: str, **kwargs): ...
def get_message(
    message_id: str,
    file_key: str | None = None,
    file_type: str | None = None,
    **kwargs,
): ...
def get_message_content(message_id: str, **kwargs): ...
def get_message_resource(message_id: str, file_key: str, type: str, **kwargs): ...
@overload
def get_messages(
    id: str,
    container_id_type: str = "chat",
    max_num_messages: int | float = float("inf"),
    max_message_length: int | float = float("inf"),
    **kwargs,
): ...
@overload
def get_messages(
    id: str,
    container_id_type: str = "chat",
    start_time: int | None = None,
    end_time: int | None = None,
    sort_type: str = "ByCreateTimeAsc",
    page_size: int = 50,
    page_token: str | None = None,
    **kwargs,
): ...
def get_messages_chain(
    message_id: str,
    max_num_messages: int | float = float("inf"),
    max_message_length: int | float = float("inf"),
    **kwargs,
): ...
def get_messages_history(
    container_id: str,
    container_id_type: str = "chat",
    start_time: int | None = None,
    end_time: int | None = None,
    sort_type: str = "ByCreateTimeAsc",
    page_size: int = 50,
    page_token: str | None = None,
    **kwargs,
): ...
@overload
def forward_message(message_id: str, receive_id: str, receive_id_type: str | None = None, uuid: str = "", **kwargs): ...
@overload
def forward_message(
    message_id_list: list[str], receive_id: str, receive_id_type: str | None = None, uuid: str = "", **kwargs
): ...
def forward_message_list(
    message_id_list: list[str],
    receive_id: str,
    receive_id_type: str | None = None,
    uuid: str = "",
    **kwargs,
): ...
def read_users(message_id: str, user_id_type: str = "open_id", **kwargs): ...
def push_follow_up(message_id: str, follow_ups: str | dict, **kwargs): ...
