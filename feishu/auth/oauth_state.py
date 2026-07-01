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

r"""
OAuth 重定向流程的 `state` 防伪：HMAC 签名 + TTL + 发起者身份绑定，抵御 CSRF / state 伪造。

[feishu.auth.oauth.OAuthNamespace.authorize_url][] 接受一个透传的 `state` 但不规定其生成与校验。
[feishu.auth.oauth_state.OAuthStateSigner][] 生成一个自包含、经 HMAC-SHA256 签名、带签发时间的 `state`，
并在回调时校验签名与有效期；`user_matches` 进一步校验「完成授权的用户」与「发起授权的用户」一致。

注意：本签名器是无状态的（仅保证完整性、时效与身份绑定）。若需严格的「一次性」语义，调用方应另以一个一次性
nonce 存储记录已消费的 `nonce`，本签名器在每个 `state` 中携带 `nonce` 以便此类去重。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from .user_tokens import user_identity_keys


def _now() -> int:
    return int(time.time())


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


@dataclass(frozen=True)
class OAuthState:
    r"""一个已校验的 OAuth `state`：发起授权的用户别名、申请的 scopes、租户、nonce 与签发时间。"""

    user_keys: tuple[str, ...]
    scopes: tuple[str, ...]
    tenant_key: str | None
    nonce: str
    issued_at: int


class OAuthStateSigner:
    r"""
    生成与校验 HMAC 签名的 OAuth `state`。

    Args:
        signing_secret: 用于 HMAC-SHA256 的密钥，不能为空。
        ttl_seconds: `state` 的有效期秒数。默认为 `600`。
        version: 版本标识，纳入签名载荷以便后续平滑升级。默认为 `"v1"`。

    Examples:
        >>> signer = OAuthStateSigner("s3cret")
        >>> state = signer.issue(user_keys=("ou_1",), scopes=("calendar:calendar",))
        >>> parsed = signer.consume(state)
        >>> parsed.user_keys, parsed.scopes
        (('ou_1',), ('calendar:calendar',))
        >>> signer.consume(state + "x") is None  # tampered signature rejected
        True
        >>> OAuthStateSigner("other").consume(state) is None  # wrong secret rejected
        True
    """

    def __init__(self, signing_secret: str, *, ttl_seconds: int = 600, version: str = "v1") -> None:
        if not signing_secret:
            raise ValueError("signing_secret must not be empty")
        self._secret = signing_secret.encode("utf-8")
        self._ttl = ttl_seconds
        self._version = version

    def _sign(self, body: str) -> str:
        return _b64encode(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())

    def issue(
        self,
        *,
        user_keys: tuple[str, ...] = (),
        scopes: tuple[str, ...] = (),
        tenant_key: str | None = None,
        nonce: str | None = None,
    ) -> str:
        r"""签发一个签名的 `state` 字符串，可绑定发起用户、scopes 与租户。"""
        payload: dict[str, Any] = {
            "v": self._version,
            "uk": list(user_keys),
            "sc": list(scopes),
            "tk": tenant_key,
            "n": nonce or uuid.uuid4().hex,
            "iat": _now(),
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        body = _b64encode(raw)
        return f"{body}.{self._sign(body)}"

    def consume(self, state: str | None) -> OAuthState | None:
        r"""校验签名与有效期，成功返回 [feishu.auth.oauth_state.OAuthState][]，否则返回 `None`。"""
        if not state or "." not in state:
            return None
        body, signature = state.rsplit(".", 1)
        if not hmac.compare_digest(self._sign(body), signature):
            return None
        try:
            payload = json.loads(_b64decode(body))
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("v") != self._version:
            return None
        issued_at = payload.get("iat")
        if not isinstance(issued_at, int) or _now() - issued_at > self._ttl:
            return None
        return OAuthState(
            user_keys=tuple(payload.get("uk") or ()),
            scopes=tuple(payload.get("sc") or ()),
            tenant_key=payload.get("tk"),
            nonce=str(payload.get("n") or ""),
            issued_at=issued_at,
        )

    def user_matches(self, state: OAuthState, callback_user: Mapping[str, Any]) -> bool:
        r"""
        校验完成授权的回调用户是否就是发起授权的用户。

        **fail-closed**：`state` 未绑定发起用户（`user_keys` 为空）时返回 `False`——无绑定就无法证明「完成授权
        者即发起者」，按零信任视作校验失败而非放行（避免任意回调用户冒用一个无主 `state`）。发起方应始终绑定
        用户（见 [feishu.auth.oauth_state.OAuthStateSigner.issue][] 的 `user_keys`）。
        """
        if not state.user_keys:
            return False
        return bool(set(user_identity_keys(callback_user)) & set(state.user_keys))
