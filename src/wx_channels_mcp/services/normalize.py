"""把接口原始记录归一化成 video / 指标（字段名照接口原文，不换算单位）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PUBLISHED_TZ = "Asia/Shanghai"
SHANGHAI = ZoneInfo(PUBLISHED_TZ)

VISIBILITY_BY_VISIBLE_TYPE = {1: "public", 2: "private"}
VISIBILITY_BY_STATUS = {0: "deleted"}


@dataclass(frozen=True)
class VideoRecord:
    video_id: str
    title: str
    published_at: str
    published_raw: int
    time_precision: str
    duration_sec: int | None
    visibility: str


def _media_duration(desc) -> int | None:
    if not isinstance(desc, dict):
        return None
    media = desc.get("media") or []
    if not media or not isinstance(media[0], dict):
        return None
    value = media[0].get("videoPlayLen")
    try:
        return int(round(float(value))) if value is not None else None
    except (TypeError, ValueError):
        return None


def _title(desc) -> str:
    if isinstance(desc, dict):
        for key in ("description", "title", "content"):
            value = desc.get(key) or (desc.get("media") or [{}])[0].get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    return ""


def to_video(post: dict) -> VideoRecord:
    """接口作品对象 -> videos 行。"""
    video_id = post.get("objectId") or post.get("exportId") or ""
    raw_create = post.get("createTime")
    published_raw = int(raw_create) if raw_create else 0
    published_at = ""
    if published_raw:
        published_at = datetime.fromtimestamp(published_raw, tz=SHANGHAI).astimezone(timezone.utc).isoformat()

    visible_type = post.get("visibleType")
    status = post.get("status")
    if status == 0:
        visibility = VISIBILITY_BY_STATUS[0]
    else:
        visibility = VISIBILITY_BY_VISIBLE_TYPE.get(visible_type, "unknown")

    return VideoRecord(
        video_id=video_id,
        title=_title(post.get("desc")),
        published_at=published_at,
        published_raw=published_raw,
        time_precision="minute",
        duration_sec=_media_duration(post.get("desc")),
        visibility=visibility,
    )


def to_metrics(post: dict) -> dict:
    """接口作品对象 -> snapshots 指标列（原名照抄，比率保持 0-1 小数）。"""
    return {
        "play_count": post.get("readCount"),
        "finish_rate": post.get("fullPlayRate"),
        "avg_watch_sec": post.get("avgPlayTimeSec"),
        "fast_flip_rate": post.get("fastFlipRate"),
        "like_count": post.get("likeCount"),
        "comment_count": post.get("commentCount"),
        "share_count": post.get("forwardCount"),
        "favorite_count": post.get("favCount"),
        "follower_delta": post.get("followCount"),
    }
