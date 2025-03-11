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


from .async_request import async_delete, async_get, async_patch, async_post, async_put
from .crypto import decrypt
from .decorators import authorize, flexible_decorator, pagination
from .request import delete, get, patch, post, put

__all__ = [
    "post",
    "async_post",
    "get",
    "async_get",
    "put",
    "async_put",
    "patch",
    "async_patch",
    "delete",
    "async_delete",
    "decrypt",
    "flexible_decorator",
    "authorize",
    "pagination",
]
