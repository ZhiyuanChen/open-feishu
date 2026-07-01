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

from .drive import DriveNamespace
from .files import FilesNamespace
from .permissions import PermissionsNamespace, infer_member_type
from .references import (
    DocumentReference,
    document_reference_from_mapping,
    meeting_note_reference_from_mapping,
    meeting_note_reference_from_meeting,
    parse_document_reference,
    raw_document_content,
    resolve_document_reference,
)

__all__ = [
    "DocumentReference",
    "DriveNamespace",
    "FilesNamespace",
    "PermissionsNamespace",
    "document_reference_from_mapping",
    "infer_member_type",
    "meeting_note_reference_from_mapping",
    "meeting_note_reference_from_meeting",
    "parse_document_reference",
    "resolve_document_reference",
    "raw_document_content",
]
