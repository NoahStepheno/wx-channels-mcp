"""字段级缺失计数：防"接口悄悄不再给某字段"（acceptance-v4 §5.4 / §8.2）。

规则：
- `(video_id, field)` 连续缺失 +1，并记录出现在哪些批次
- 连续缺失 >= 3 且跨 >= 2 个不同批次 -> `status=unsupported`
- 任一次读到该字段 -> 计数清零、状态回 `counting`
"""

from __future__ import annotations

import json
import sqlite3

MIN_CONSECUTIVE_MISSING = 3
MIN_DISTINCT_BATCHES = 2

COUNTING = "counting"
UNSUPPORTED = "unsupported"


def _loads(value, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def record_absence(
    conn: sqlite3.Connection,
    video_id: str,
    missing_fields: list[str],
    *,
    batch_id: str,
    checked_at: str,
) -> list[str]:
    """记录一次抓取里缺失的字段，返回本次升级为 unsupported 的字段。"""
    upgraded: list[str] = []
    for field in sorted(set(missing_fields)):
        row = conn.execute(
            "SELECT * FROM field_absence WHERE video_id = ? AND field = ?",
            (video_id, field),
        ).fetchone()

        if row is None:
            conn.execute(
                "INSERT INTO field_absence"
                " (video_id, field, consecutive_missing, first_missing_at, last_checked_at, batches_seen, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (video_id, field, 1, checked_at, checked_at, json.dumps([batch_id]), COUNTING),
            )
            continue

        batches = _loads(row["batches_seen"], [])
        status = row["status"]
        consecutive = int(row["consecutive_missing"] or 0)

        if batch_id in batches:
            # 同一批次内重试：只推进检查时间，不计数
            conn.execute(
                "UPDATE field_absence SET last_checked_at = ? WHERE video_id = ? AND field = ?",
                (checked_at, video_id, field),
            )
            continue

        batches.append(batch_id)
        consecutive += 1

        if status != UNSUPPORTED and consecutive >= MIN_CONSECUTIVE_MISSING and len(batches) >= MIN_DISTINCT_BATCHES:
            status = UNSUPPORTED
            upgraded.append(field)

        conn.execute(
            "UPDATE field_absence SET consecutive_missing = ?, last_checked_at = ?,"
            " batches_seen = ?, status = ? WHERE video_id = ? AND field = ?",
            (consecutive, checked_at, json.dumps(batches), status, video_id, field),
        )

    conn.commit()
    return upgraded


def record_present(
    conn: sqlite3.Connection,
    video_id: str,
    present_fields: list[str],
    *,
    checked_at: str,
) -> None:
    """读到字段即清零；未出现过的字段不建行。"""
    for field in sorted(set(present_fields)):
        row = conn.execute(
            "SELECT consecutive_missing FROM field_absence WHERE video_id = ? AND field = ?",
            (video_id, field),
        ).fetchone()
        if row is None:
            continue
        conn.execute(
            "UPDATE field_absence SET consecutive_missing = 0, last_checked_at = ?, status = ?"
            " WHERE video_id = ? AND field = ?",
            (checked_at, COUNTING, video_id, field),
        )
    conn.commit()


def get_absence(conn: sqlite3.Connection, video_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM field_absence WHERE video_id = ? ORDER BY field", (video_id,)
    ).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        data["batches_seen"] = _loads(data.get("batches_seen"), [])
        out.append(data)
    return out
