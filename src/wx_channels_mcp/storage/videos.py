"""作品表写入：首次插入，之后只推进 last_seen_at（标题/时长允许补空）。"""

from __future__ import annotations

import sqlite3

from wx_channels_mcp.services.normalize import VideoRecord


def get_video(conn: sqlite3.Connection, video_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()
    return dict(row) if row else None


def upsert_video(conn: sqlite3.Connection, video: VideoRecord, *, seen_at: str, account_id: str = "") -> bool:
    """返回 True 表示本次是首次见到该作品。"""
    existing = get_video(conn, video.video_id)
    if existing is None:
        conn.execute(
            "INSERT INTO videos (video_id, account_id, title, published_at, time_precision,"
            " published_tz, published_raw, duration_sec, url, visibility, first_seen_at, last_seen_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                video.video_id,
                account_id,
                video.title,
                video.published_at,
                video.time_precision,
                "Asia/Shanghai",
                str(video.published_raw),
                video.duration_sec,
                None,
                video.visibility,
                seen_at,
                seen_at,
            ),
        )
        conn.commit()
        return True

    # 只补空，不覆盖已知值（与快照冻结同一条纪律）
    updates: dict[str, object] = {"last_seen_at": seen_at}
    for column, value in (
        ("title", video.title),
        ("duration_sec", video.duration_sec),
        ("published_at", video.published_at),
        ("visibility", video.visibility),
    ):
        if existing.get(column) in (None, "") and value not in (None, ""):
            updates[column] = value
    assignments = ", ".join(f"{column} = ?" for column in updates)
    conn.execute(
        f"UPDATE videos SET {assignments} WHERE video_id = ?",
        [*updates.values(), video.video_id],
    )
    conn.commit()
    return False
