# OpenFeishu
# Copyright (C) 2024-Present  DanLing

# This file is part of OpenFeishu.

# OpenFeishu is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# OpenFeishu is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# For additional terms and clarifications, please refer to our License FAQ at:
# <https://multimolecule.danling.org/about/license-faq>.

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
from os import PathLike
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..consts import REGION_BASE_URLS
from .bundles.registry import available_bundle_names


@dataclass(frozen=True)
class ConfigurationIssue:
    """One path-specific problem found before agent resources are created."""

    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


class AgentConfigurationError(ValueError):
    """Raised when the default Agent assembly cannot use the supplied configuration."""

    def __init__(self, issues: Sequence[ConfigurationIssue]) -> None:
        self.issues = tuple(issues)
        details = "\n".join(f"- {issue}" for issue in self.issues)
        super().__init__(f"invalid agent configuration:\n{details}")


_SECTIONS = (
    "feishu",
    "model",
    "fast_model",
    "oauth",
    "storage",
    "server",
    "ws",
    "approval",
    "shared_files",
    "session",
    "bundle",
    "message",
    "reply",
    "memory",
    "runtime",
)


def validate_agent_config(
    config: Mapping[str, Any] | None,
    *,
    injected: Iterable[str] = (),
) -> tuple[ConfigurationIssue, ...]:
    """Return configuration errors without constructing clients, stores, or user factories.

    ``injected`` names dependencies provided to ``Agent`` directly, such as ``engine``,
    ``client``, ``backend``, ``model_client``, and summarizers. Unknown application-specific
    config sections and keys are deliberately left alone.
    """
    issues: list[ConfigurationIssue] = []
    if config is None:
        config = {}
    if not isinstance(config, Mapping):
        return (ConfigurationIssue("config", "must be a mapping"),)

    supplied = frozenset(injected)
    sections: dict[str, Mapping[str, Any]] = {}
    for name in _SECTIONS:
        section_value = config.get(name)
        if section_value is None:
            sections[name] = {}
        elif not isinstance(section_value, Mapping):
            issues.append(ConfigurationIssue(name, "must be a mapping"))
            sections[name] = {}
        else:
            sections[name] = section_value

    def get_value(path: str) -> Any:
        section, _, key = path.partition(".")
        return sections[section].get(key) if key else config.get(section)

    def issue(field: str, message: str) -> None:
        issues.append(ConfigurationIssue(field, message))

    def require_text(path: str) -> None:
        current = get_value(path)
        if not isinstance(current, str) or not current.strip():
            issue(path, "is required and must be a non-empty string")

    def check_bool(path: str) -> None:
        current = get_value(path)
        if current is not None and not isinstance(current, bool):
            issue(path, "must be a boolean")

    def check_number(path: str, *, minimum: float = 0, allow_none: bool = False) -> None:
        current = get_value(path)
        if current is None and allow_none:
            return
        if current is not None and (isinstance(current, bool) or not isinstance(current, Real) or current < minimum):
            relation = "positive" if minimum > 0 else "non-negative"
            issue(path, f"must be a {relation} number" if not allow_none else f"must be a {relation} number or null")

    def check_integer(path: str, *, minimum: int = 0) -> None:
        current = get_value(path)
        if current is not None and (isinstance(current, bool) or not isinstance(current, int) or current < minimum):
            relation = "positive" if minimum > 0 else "non-negative"
            issue(path, f"must be a {relation} integer")

    def check_url(path: str) -> None:
        current = get_value(path)
        if current is None:
            return
        if not isinstance(current, str):
            issue(path, "must be an absolute http or https URL")
            return
        try:
            parsed = urlsplit(current)
            valid = parsed.scheme in {"http", "https"} and bool(parsed.netloc and parsed.hostname)
        except ValueError:
            valid = False
        if not valid:
            issue(path, "must be an absolute http or https URL")

    for path in ("feishu.app_id", "feishu.app_secret"):
        if "engine" not in supplied and "client" not in supplied:
            require_text(path)

    if "engine" not in supplied and "backend" not in supplied:
        require_text("model.model")
        if "model_client" not in supplied:
            require_text("model.api_key")
            require_text("model.base_url")

    port = get_value("server.port")
    if port is not None and (isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535):
        issue("server.port", "must be an integer between 1 and 65535")

    for path in ("reply.stream", "memory.enabled", "time_aware_system", "model.thinking_enabled"):
        check_bool(path)

    for path in (
        "ws.card_ack_timeout_seconds",
        "reply.progress_summary_interval_seconds",
        "session.idle_session_timeout_seconds",
        "fast_model.timeout_seconds",
        "fast_model.summary_timeout_seconds",
    ):
        check_number(
            path,
            minimum=(
                0
                if path
                in {
                    "ws.card_ack_timeout_seconds",
                    "reply.progress_summary_interval_seconds",
                    "session.idle_session_timeout_seconds",
                }
                else 0.000001
            ),
            allow_none=path == "ws.card_ack_timeout_seconds",
        )

    for path in ("session.summarize_threshold_tokens", "session.summarize_keep_recent", "session.max_messages"):
        check_integer(path)
    for path in (
        "oauth.state_ttl_seconds",
        "oauth.authorization_ttl_seconds",
        "shared_files.ttl_seconds",
        "shared_files.max_bytes",
        "server.seen_ttl_seconds",
        "model.thinking_budget",
        "fast_model.max_chars",
        "fast_model.summary_max_chars",
    ):
        check_integer(path, minimum=1)
    check_number("runtime.shutdown_timeout_seconds", minimum=0.000001)

    for path in ("model.base_url", "fast_model.base_url", "oauth.public_url"):
        check_url(path)

    timezone = config.get("timezone")
    if timezone is not None:
        if not isinstance(timezone, str) or not timezone.strip():
            issue("timezone", "must be a valid IANA timezone name")
        else:
            try:
                ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError):
                issue("timezone", "must be a valid IANA timezone name")

    toolkits = config.get("toolkits", ("feishu.workplace",))
    if toolkits is not None:
        if isinstance(toolkits, str) or not isinstance(toolkits, Sequence):
            issue("toolkits", "must be a sequence of bundle names")
        elif any(not isinstance(item, str) or not item.strip() for item in toolkits):
            issue("toolkits", "each bundle name must be a non-empty string")
        elif "registry" not in supplied:
            known_bundles = set(available_bundle_names())
            for name in toolkits:
                if name not in known_bundles:
                    issue(
                        "toolkits", f"unknown bundle {name!r}; registered bundles: {', '.join(sorted(known_bundles))}"
                    )
            if "memory" in toolkits and get_value("memory.enabled") is not True and "memory_store" not in supplied:
                issue("toolkits", "the memory bundle requires memory.enabled=true or an injected memory_store")

    for path in ("server.host", "server.service"):
        current = get_value(path)
        if current is not None and (not isinstance(current, str) or not current.strip()):
            issue(path, "must be a non-empty string")

    region = get_value("feishu.region")
    if region is not None:
        if not isinstance(region, str) or not region.strip():
            issue("feishu.region", "must be a supported region name")
        elif region not in REGION_BASE_URLS:
            issue("feishu.region", f"unknown region {region!r}; expected one of {', '.join(sorted(REGION_BASE_URLS))}")

    seen_store = get_value("server.seen_store")
    if seen_store is not None:
        mode = str(seen_store).strip().lower()
        if mode not in {"", "none", "off", "false", "0", "disabled", "sqlite"}:
            issue("server.seen_store", "must be 'sqlite' or a disabled value")

    for path in (
        "server.health_path",
        "server.ready_path",
        "server.event_path",
        "server.card_path",
        "oauth.callback_path",
    ):
        current = get_value(path)
        if current is None or (path == "server.card_path" and current == ""):
            continue
        if not isinstance(current, str) or not current.startswith("/"):
            issue(path, "must be an absolute route path starting with '/'")

    event_path = get_value("server.event_path") or "/feishu/event"
    card_path = get_value("server.card_path") or "/feishu/card"
    route_paths = [
        ("server.health_path", get_value("server.health_path") or "/health"),
        ("server.ready_path", get_value("server.ready_path") or "/ready"),
        ("server.event_path", event_path),
    ]
    if card_path != event_path:
        route_paths.append(("server.card_path", card_path))
    oauth_enabled = (
        "engine" not in supplied
        and bool(get_value("oauth.public_url"))
        and bool(get_value("oauth.state_secret") or get_value("feishu.app_secret"))
    )
    if oauth_enabled:
        route_paths.append(("oauth.callback_path", get_value("oauth.callback_path") or "/oauth/callback"))
    used_paths: dict[str, str] = {}
    for field, path in route_paths:
        if not isinstance(path, str):
            continue
        previous = used_paths.get(path)
        if previous is not None:
            issue(field, f"must differ from {previous}; both resolve to {path!r}")
        else:
            used_paths[path] = field

    for path in ("storage.path", "storage.audit_path", "system_path"):
        current = get_value(path)
        if current is not None and (not isinstance(current, str | PathLike) or not str(current).strip()):
            issue(path, "must be a non-empty filesystem path")

    system = config.get("system")
    if system is not None and not isinstance(system, str) and not callable(system):
        issue("system", "must be prompt text or a callable")
    system_path = config.get("system_path")
    if system is None and isinstance(system_path, str | PathLike) and str(system_path).strip():
        try:
            Path(system_path).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            issue("system_path", "must name a readable UTF-8 prompt file")

    fast_model = get_value("fast_model.model") or get_value("fast_model.name")
    if fast_model is not None:
        if not isinstance(fast_model, str) or not fast_model.strip():
            issue("fast_model.model", "must be a non-empty string")
        if "engine" not in supplied and not {
            "progress_summarizer",
            "text_summarizer",
        }.issubset(supplied):
            if not (get_value("fast_model.api_key") or get_value("model.api_key")):
                issue("fast_model.api_key", "is required with fast_model.model unless model.api_key is set")
            if not (get_value("fast_model.base_url") or get_value("model.base_url")):
                issue("fast_model.base_url", "is required with fast_model.model unless model.base_url is set")

    return tuple(issues)


__all__ = ["AgentConfigurationError", "ConfigurationIssue", "validate_agent_config"]
