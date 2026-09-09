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

from collections.abc import Mapping
from typing import Any


def custom_field_text(value: Any) -> str:
    r"""
    从 Directory 自定义字段的 `text_value` 取出一段纯文本。

    支持裸字符串，或飞书的 `{default_value, i18n_value}` 结构。不解释字段业务含义。
    """
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    default = str(value.get("default_value") or "").strip()
    if default:
        return default
    translations = value.get("i18n_value") or {}
    if not isinstance(translations, dict):
        return ""
    for locale in ("en_us", "zh_cn", "ja_jp"):
        translated = str(translations.get(locale) or "").strip()
        if translated:
            return translated
    return next((str(item).strip() for item in translations.values() if str(item).strip()), "")


def custom_fields_by_key(base_info: Mapping[str, Any]) -> dict[str, str]:
    r"""
    将 `base_info.custom_field_values` 展成 `{field_key: text}`。

    调用方自行把 `field_key` 映射到业务名；SDK 不内置任何租户字段表。
    """
    values: dict[str, str] = {}
    for raw in base_info.get("custom_field_values") or []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("field_key") or "").strip()
        if key:
            values[key] = custom_field_text(raw.get("text_value"))
    return values


__all__ = ["custom_field_text", "custom_fields_by_key"]
