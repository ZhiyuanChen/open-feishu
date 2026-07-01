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

from .._url import quote_segment

# --- CardKit v1 wire facts -------------------------------------------------
# Keep every wire value as a single-point constant. Unit tests reference these
# symbols, not hardcoded literals, so a future platform correction stays local.

# Base path for card entities, shared by creation and card_id-derived content/settings paths.
CARDS_PATH = "cardkit/v1/cards"
# 1) Create card entity: POST /cardkit/v1/cards. Keep a separate alias for intent.
CREATE_CARD_PATH = CARDS_PATH
CREATE_CARD_TYPE_FIELD = "type"
CREATE_CARD_TYPE = "card_json"  # verified against the live CardKit API (smoke test), not "card"
CREATE_CARD_DATA_FIELD = "data"  # body["data"] = json.dumps(card)

# 2) Send interactive message: POST /im/v1/messages?receive_id_type=...
#    or, in reply position: POST /im/v1/messages/{message_id}/reply (no receive_id).
SEND_MESSAGE_PATH = "im/v1/messages"
SEND_MESSAGE_TYPE = "interactive"
SEND_CARD_CONTENT_TYPE = "card"  # inner content {"type": "card", "data": {"card_id": ...}}

# 3) Stream cumulative text: PUT /cardkit/v1/cards/{card_id}/elements/{element_id}/content
CONTENT_FIELD = "content"  # verified against the live CardKit API (smoke test), not "text"
SEQUENCE_FIELD = "sequence"
UUID_FIELD = "uuid"

# 4) Finalize: PATCH /cardkit/v1/cards/{card_id}/settings
SETTINGS_FIELD = "settings"
STREAMING_MODE_KEY = "streaming_mode"

# Per-card write cap documented by Feishu (10 ops/s/card).
MAX_OPS_PER_SEC = 10


def content_path(card_id: str, element_id: str) -> str:
    return f"{CARDS_PATH}/{quote_segment(card_id)}/elements/{quote_segment(element_id)}/content"


def settings_path(card_id: str) -> str:
    return f"{CARDS_PATH}/{quote_segment(card_id)}/settings"


def reply_message_path(message_id: str) -> str:
    return f"{SEND_MESSAGE_PATH}/{quote_segment(message_id)}/reply"
