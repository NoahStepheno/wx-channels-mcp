"""本地 CSV 导出：给复盘记录表直接映射用（与平台"下载"无关）。"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

COLUMNS = (
    "video_id",
    "published_at",
    "snapshot_kind",
    "offset_days",
    "on_time",
    "snapshot_late",
    "play_count",
    "finish_rate",
    "avg_watch_sec",
    "like_count",
    "comment_count",
    "share_count",
    "favorite_count",
    "follower_delta",
    "completeness",
    "anchor_status",
    "replay_suspected",
)

_QUERY = """
SELECT s.video_id, v.published_at, s.snapshot_kind, s.offset_days, s.on_time, s.snapshot_late,
       s.play_count, s.finish_rate, s.avg_watch_sec, s.like_count, s.comment_count,
       s.share_count, s.favorite_count, s.follower_delta, s.completeness,
       s.anchor_status, s.replay_suspected
FROM snapshots s
LEFT JOIN videos v ON v.video_id = s.video_id
{where}
ORDER BY v.published_at DESC, s.snapshot_kind
"""


def export_rows(conn: sqlite3.Connection, *, since: str | None = None) -> list[dict]:
    """导出快照行；`since` 为 ISO 日期，按 published_at 过滤。"""
    where = ""
    params: tuple = ()
    if since:
        where = "WHERE v.published_at >= ?"
        params = (since,)
    rows = conn.execute(_QUERY.format(where=where), params).fetchall()
    return [dict(row) for row in rows]


def export_csv(conn: sqlite3.Connection, path: str | Path, *, since: str | None = None) -> int:
    """写 CSV，返回行数。"""
    rows = export_rows(conn, since=since)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)
