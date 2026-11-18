"""SQLite 连接与建表。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def init_db(conn: sqlite3.Connection) -> sqlite3.Connection:
    """执行 schema（幂等），返回同一连接。"""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def connect(path: str | Path) -> sqlite3.Connection:
    """打开（必要时创建）数据库并建表。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return init_db(conn)
