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

from .credentials import AppTicketStore, Credential, InMemoryAppTicketStore, InternalCredential, StoreCredential
from .oauth import OAuthNamespace
from .oauth_state import OAuthState, OAuthStateSigner
from .tokens import CachedToken, InMemoryTokenCache, TokenCache, TokenManager
from .user_tokens import (
    InMemoryOAuthTokenStore,
    OAuthTokenStore,
    SqliteOAuthTokenStore,
    TokenRecord,
    UserTokenProvider,
    user_from_identity_keys,
    user_identity_keys,
)

__all__ = [
    "AppTicketStore",
    "CachedToken",
    "Credential",
    "InMemoryAppTicketStore",
    "InMemoryTokenCache",
    "InternalCredential",
    "OAuthNamespace",
    "StoreCredential",
    "TokenCache",
    "TokenManager",
    # User-scoped OAuth: per-user token persistence + refresh-aware execution.
    "OAuthTokenStore",
    "InMemoryOAuthTokenStore",
    "SqliteOAuthTokenStore",
    "TokenRecord",
    "UserTokenProvider",
    "user_from_identity_keys",
    "user_identity_keys",
    # OAuth redirect-flow CSRF protection.
    "OAuthState",
    "OAuthStateSigner",
]
