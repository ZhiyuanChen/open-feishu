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

from .chats import ChatNamespace
from .inbound import (
    card_text,
    card_title,
    collect_card_text,
    interactive_card_text,
    is_mentioned,
    message_body_text,
    message_content,
    message_resource,
    message_resources,
    message_sender_label,
    message_text,
    message_transcript,
)
from .messages import IMNamespace, infer_msg_type, infer_receive_id_type
from .pins import PinsNamespace
from .reactions import ReactionsNamespace

__all__ = [
    "ChatNamespace",
    "IMNamespace",
    "PinsNamespace",
    "ReactionsNamespace",
    "card_text",
    "card_title",
    "collect_card_text",
    "infer_msg_type",
    "infer_receive_id_type",
    "interactive_card_text",
    "is_mentioned",
    "message_body_text",
    "message_content",
    "message_resource",
    "message_resources",
    "message_sender_label",
    "message_text",
    "message_transcript",
]
