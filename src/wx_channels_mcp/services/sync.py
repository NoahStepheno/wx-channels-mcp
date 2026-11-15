"""拉取 + 归一化 + 分桶 + 落库（支持 dry-run 与 JSON 摘要）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from wx_channels_mcp.config import DEFAULT_LOOKBACK_DAYS, DEFAULT_PAGE_SIZE
from wx_channels_mcp.fetch.errors import WxError
from wx_channels_mcp.rules.window import classify_snapshot
from wx_channels_mcp.services.absence import record_absence, record_present
from wx_channels_mcp.services.normalize import to_metrics, to_video
from wx_channels_mcp.storage.snapshots import freeze_snapshot
from wx_channels_mcp.storage.videos import upsert_video

MAX_PAGES = 10


@dataclass
class SyncSummary:
    status: str
    fetched_at: str
    lookback_days: int
    dry_run: bool
    videos: int = 0
    snapshots: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    error: dict | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "fetched_at": self.fetched_at,
            "lookback_days": self.lookback_days,
            "dry_run": self.dry_run,
            "videos": self.videos,
            "snapshots": self.snapshots,
            "skipped": self.skipped,
            "error": self.error,
        }


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def sync(
    api,
    conn=None,
    *,
    dry_run: bool = False,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    page_size: int = DEFAULT_PAGE_SIZE,
    now: datetime | None = None,
    max_pages: int = MAX_PAGES,
    json_path: str | Path | None = None,
    parser_version: str = "0.1.0",
) -> SyncSummary:
    """执行一次同步。

    dry_run=True 时只做取数/归一化/分桶，返回将要落库的行，不写库。
    """
    moment = _now(now)
    fetched_at = moment.isoformat()
    batch_id = f"batch-{moment.strftime('%Y%m%dT%H%M%S')}Z"
    start_time = int((moment - timedelta(days=lookback_days)).timestamp())
    end_time = int(moment.timestamp())
    summary = SyncSummary(
        status="ok",
        fetched_at=fetched_at,
        lookback_days=lookback_days,
        dry_run=dry_run,
    )

    for page in range(1, max_pages + 1):
        try:
            data = api.list_posts(
                page_size=page_size,
                current_page=page,
                start_time=start_time,
                end_time=end_time,
                timestamp_ms=str(int(moment.timestamp() * 1000)),
            )
        except WxError as exc:
            summary.status = exc.reason
            summary.error = {"reason": exc.reason, "message": str(exc), "detail": exc.detail[:200]}
            break

        posts = ((data.get("data") or {}).get("list")) or []
        _handle_posts(posts, summary, conn, dry_run, moment, fetched_at, parser_version, batch_id)
        if len(posts) < page_size:
            break

    if json_path:
        Path(json_path).write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _handle_posts(posts, summary, conn, dry_run, moment, fetched_at, parser_version, batch_id) -> None:
    for post in posts:
        video = to_video(post)
        metrics = to_metrics(post)
        summary.videos += 1

        if not video.published_raw:
            summary.skipped.append({"video_id": video.video_id, "reason": "no_published_at"})
            continue

        offset_days = (moment - datetime.fromtimestamp(video.published_raw, tz=timezone.utc)).total_seconds() / 86400
        # 首次接入且已过 +7（含 +3 窗口早已过去） -> backfill，不参与 +3/+7 对比
        first_seen = not dry_run and conn is not None and upsert_video(conn, video, seen_at=fetched_at)
        bucket = classify_snapshot(offset_days, precision="minute", first_seen=bool(first_seen))

        if bucket.kind is None:
            summary.skipped.append(
                {"video_id": video.video_id, "reason": "not_in_window", "offset_days": round(offset_days, 3)}
            )
            continue

        row: dict[str, Any] = {
            "video_id": video.video_id,
            "snapshot_kind": bucket.kind,
            "fetched_at": fetched_at,
            "offset_days": round(offset_days, 6),
            "on_time": bucket.on_time,
            "snapshot_late": bucket.late,
            "duration_sec": video.duration_sec,
            "rate_unit": "ratio",
            "completeness": "full" if metrics.get("play_count") is not None else "partial",
            "field_missing": [k for k, v in metrics.items() if v is None],
            "parser_version": parser_version,
            **metrics,
        }

        preview = {
            "video_id": video.video_id,
            "snapshot_kind": bucket.kind,
            "offset_days": round(offset_days, 3),
            "on_time": bucket.on_time,
            "snapshot_late": bucket.late,
            "play_count": metrics.get("play_count"),
        }

        if dry_run or conn is None:
            summary.snapshots.append(preview)
            continue

        result = freeze_snapshot(conn, row)
        stored = _stored_anchor(conn, video.video_id, bucket.kind)
        missing = row["field_missing"]
        present = [field for field in metrics if field not in missing]
        if missing:
            record_absence(conn, video.video_id, missing, batch_id=batch_id, checked_at=fetched_at)
        if present:
            record_present(conn, video.video_id, present, checked_at=fetched_at)
        preview.update(
            {
                "inserted": result.inserted,
                "enriched": list(result.enriched),
                "anchor_status": stored[0] if stored else None,
                "replay_suspected": stored[1] if stored else None,
            }
        )
        summary.snapshots.append(preview)


def _stored_anchor(conn, video_id: str, kind: str):
    row = conn.execute(
        "SELECT anchor_status, replay_suspected FROM snapshots WHERE video_id = ? AND snapshot_kind = ?",
        (video_id, kind),
    ).fetchone()
    if row is None:
        return None
    return row["anchor_status"], bool(row["replay_suspected"])
