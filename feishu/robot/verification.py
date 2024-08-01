# open-feishu
# Copyright (C) 2024-Present  Zhiyuan Chen

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import annotations

from feishu import variables


def handle_verification(request: dict) -> dict:
    r"""
    处理飞书URL验证请求

    飞书文档:
        [配置订阅方式](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case)
    """
    if request.get("token") and variables.VERIFICATION_TOKEN and request["token"] != variables.VERIFICATION_TOKEN:
        raise ValueError("Invalid verification token")
    return {"challenge": request["challenge"]}
