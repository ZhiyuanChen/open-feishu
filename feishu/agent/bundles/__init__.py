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

from .registry import BUNDLES, Bundle, BundleContext, build_tool_registry
from .workplace import (
    APPROVAL_SCOPES,
    BITABLE_SCOPES,
    BOARD_SCOPES,
    CALENDAR_READ_SCOPES,
    CALENDAR_SCOPES,
    CONTACT_SCOPES,
    DOC_READ_SCOPES,
    DOC_WRITE_SCOPES,
    DRIVE_SCOPES,
    MAIL_FOLDER_READ_SCOPES,
    MAIL_READ_SCOPES,
    MAIL_SEND_SCOPES,
    ROOM_SCOPES,
    SHEET_SCOPES,
    TASK_COMMENT_SCOPES,
    TASK_DELETE_SCOPES,
    TASK_READ_SCOPES,
    TASK_SCOPES,
    TASK_WRITE_SCOPES,
    VC_SCOPES,
    FeishuWorkplaceBundle,
)

__all__ = [
    "BUNDLES",
    "Bundle",
    "BundleContext",
    "build_tool_registry",
    "FeishuWorkplaceBundle",
    "CALENDAR_READ_SCOPES",
    "CALENDAR_SCOPES",
    "ROOM_SCOPES",
    "DOC_READ_SCOPES",
    "TASK_READ_SCOPES",
    "TASK_WRITE_SCOPES",
    "TASK_DELETE_SCOPES",
    "TASK_SCOPES",
    "BITABLE_SCOPES",
    "APPROVAL_SCOPES",
    "VC_SCOPES",
    "DOC_WRITE_SCOPES",
    "DRIVE_SCOPES",
    "SHEET_SCOPES",
    "CONTACT_SCOPES",
    "BOARD_SCOPES",
    "TASK_COMMENT_SCOPES",
    "MAIL_READ_SCOPES",
    "MAIL_SEND_SCOPES",
    "MAIL_FOLDER_READ_SCOPES",
]
