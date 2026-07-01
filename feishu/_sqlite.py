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
SQLite 连接的共享构造：WAL 模式、合理 PRAGMA，以及对「含敏感数据」库的权限收紧。

注意：WAL 模式会把已提交数据写入 `-wal` 旁文件，且该旁文件可能在检查点后被删除并按 umask 重新创建为
全局可读。因此仅对主库文件 `chmod 0o600` 不足以保护落盘的令牌：本模块的首要防线是把数据目录收紧为 `0o700`
（其他用户无法进入该目录，从而无论旁文件自身权限如何都读不到），并对已存在的库文件与旁文件补充 `0o600`。
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from pathlib import Path


def secure(db_path: str | Path) -> None:
    r"""将主库文件与 `-wal`/`-shm` 旁文件的权限收紧为 `0o600`（不存在则跳过）。"""
    path = str(db_path)
    for suffix in ("", "-wal", "-shm"):
        with contextlib.suppress(OSError):  # a sidecar may not exist
            os.chmod(path + suffix, 0o600)


def connect(db_path: str | Path) -> sqlite3.Connection:
    r"""
    打开一个 WAL 模式的 SQLite 连接，并收紧含敏感数据库的权限。

    收紧顺序：把数据目录设为 `0o700`（首要防线，防止其他用户进入目录读取任何旁文件）→ 以 `0o600` 预创建
    主库文件（umask 无关）→ 启用 WAL/同步/忙等 PRAGMA → 对已存在的库与旁文件补 `0o600`。

    Args:
        db_path: SQLite 数据库文件路径。

    Returns:
        已配置的 [sqlite3.Connection][]（`check_same_thread=False`，由上层以锁串行化访问）。
    """
    path = str(db_path)
    if path == ":memory:":
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
        # primary defense for token-at-rest: deny other users traversal into the data dir
        with contextlib.suppress(OSError):
            os.chmod(parent, 0o700)
    if not os.path.exists(path):
        # create with tight perms up front, independent of the process umask
        with contextlib.suppress(OSError):
            os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")  # wait, don't fail, when another connection holds the write lock
    secure(path)
    return conn
