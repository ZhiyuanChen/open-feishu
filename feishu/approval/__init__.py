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

from .approval import ApprovalNamespace
from .builders import (
    APPROVAL_API_UNSUPPORTED_WIDGET_TYPES,
    approval_account_label,
    approval_account_number,
    approval_account_widgets,
    approval_cached_definition_summary,
    approval_definition_code,
    approval_definition_index,
    approval_definition_may_contain_file_widget,
    approval_definition_schema,
    approval_definition_summary,
    approval_definition_widgets,
    approval_field_key,
    approval_file_fields,
    approval_form,
    approval_form_field,
    approval_form_payload,
    approval_form_payloads,
    approval_form_problems,
    approval_instance,
    approval_instance_participant_ids,
    is_approval_file_widget,
    is_approval_file_widget_text,
)
from .comments import CommentsNamespace
from .definitions import DefinitionsNamespace
from .files import (
    FilesNamespace,
    approval_file_code,
    approval_file_type_for_media_type,
    normalize_approval_file_upload_response,
)
from .instances import InstancesNamespace
from .tasks import TasksNamespace

__all__ = [
    "APPROVAL_API_UNSUPPORTED_WIDGET_TYPES",
    "ApprovalNamespace",
    "approval_account_label",
    "approval_account_number",
    "approval_account_widgets",
    "approval_instance_participant_ids",
    "CommentsNamespace",
    "DefinitionsNamespace",
    "FilesNamespace",
    "InstancesNamespace",
    "TasksNamespace",
    "approval_cached_definition_summary",
    "approval_definition_code",
    "approval_definition_index",
    "approval_definition_may_contain_file_widget",
    "approval_definition_schema",
    "approval_definition_summary",
    "approval_definition_widgets",
    "approval_field_key",
    "approval_file_code",
    "approval_file_fields",
    "approval_file_type_for_media_type",
    "approval_form",
    "approval_form_field",
    "approval_form_payload",
    "approval_form_payloads",
    "approval_form_problems",
    "approval_instance",
    "is_approval_file_widget",
    "is_approval_file_widget_text",
    "normalize_approval_file_upload_response",
]
