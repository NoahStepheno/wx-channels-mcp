"""快照写入：冻结、补空、不覆盖已冻结数值（acceptance-v4 §5.3）。

规则落地：
1. 唯一键 `(video_id, snapshot_kind)`；首次成功写入即冻结。
2. 重跑只推进 `last_seen_at`，**不覆盖**任何已冻结的数值（含 late 标记）。
3. 已冻结行只允许补空：新值非空、旧值为 NULL 才写入，并记 `enriched_fields[field] = 本次 fetched_at`。
4. `play_count = 0` 是有效值，不参与"是否为空"的判断（一律 `is None`）。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from wx_channels_mcp.rules.anchors import (
    INSUFFICIENT,
    REPLAY_SUSPECTED,
    UNIT_MISMATCH,
    Consistency,
    check_play_consistency,
)

# 允许"只补空"的指标列（顺序即补数优先级，仅用于稳定输出）
ENRICHABLE_COLUMNS = (
    "play_count",
    "finish_rate",
    "avg_watch_sec",
    "like_count",
    "comment_count",
    "share_count",
    "favorite_count",
    "follower_delta",
    "period",
)

_INSERT_COLUMNS = (
    "video_id",
    "snapshot_kind",
    "fetched_at",
    "last_seen_at",
    "offset_days",
    "on_time",
    "snapshot_late",
    *ENRICHABLE_COLUMNS,
    "rate_unit",
    "anchor_status",
    "anchor_unverified",
    "replay_suspected",
    "unit_mismatch_fields",
    "anchor_checked_at",
    "anchor_evidence",
    "completeness",
    "field_missing",
    "enriched_fields",
    "parser_version",
)


@dataclass(frozen=True)
class FreezeResult:
    inserted: bool
    enriched: tuple[str, ...] = ()


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def get_snapshot(conn: sqlite3.Connection, video_id: str, kind: str) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM snapshots WHERE video_id = ? AND snapshot_kind = ?",
        (video_id, kind),
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["field_missing"] = _loads(data.get("field_missing"), [])
    data["enriched_fields"] = _loads(data.get("enriched_fields"), {})
    data["unit_mismatch_fields"] = _loads(data.get("unit_mismatch_fields"), [])
    data["anchor_evidence"] = _loads(data.get("anchor_evidence"), {})
    return data


def _anchor(row: dict) -> Consistency:
    """锚点 B：均播 / 时长 / 完播率三者自洽性。"""
    return check_play_consistency(
        avg_play_sec=row.get("avg_watch_sec"),
        duration_sec=row.get("duration_sec"),
        full_play_rate=row.get("finish_rate"),
    )


def _apply_anchor(values: dict, anchor: Consistency, row: dict, checked_at: str) -> None:
    values["anchor_status"] = anchor.status
    values["anchor_checked_at"] = checked_at
    avg = row.get("avg_watch_sec")
    dur = row.get("duration_sec")
    values["anchor_evidence"] = {
        "avg_watch_sec": avg,
        "duration_sec": dur,
        "finish_rate": row.get("finish_rate"),
        "ratio": (avg / dur) if (avg and dur) else None,
        "reason": anchor.reason,
    }
    # 只有 A、B 都过才算"口径可信"；insufficient_data 一律未验证
    values["anchor_unverified"] = 0 if (anchor.status in ("normal", REPLAY_SUSPECTED)) else 1
    values["replay_suspected"] = 1 if anchor.status == REPLAY_SUSPECTED else 0
    if anchor.status == UNIT_MISMATCH:
        values["unit_mismatch_fields"] = ["avg_watch_sec", "finish_rate"]
    else:
        values.setdefault("unit_mismatch_fields", [])


def freeze_snapshot(conn: sqlite3.Connection, row: dict) -> FreezeResult:
    """写入一条快照；已存在则只补空 + 推进 last_seen_at。"""
    row = dict(row)
    anchor = _anchor(row)
    duration_sec = row.get("duration_sec")
    row.pop("duration_sec", None)
    row["_anchor_row"] = {"duration_sec": duration_sec}
    video_id = row["video_id"]
    kind = row["snapshot_kind"]
    fetched_at = row["fetched_at"]
    existing = _select_row(conn, video_id, kind)

    if existing is None:
        values = dict(row)
        values.setdefault("last_seen_at", fetched_at)
        values.setdefault("rate_unit", "ratio")
        values.setdefault("completeness", "full")
        values["field_missing"] = _dumps(values.get("field_missing") or [])
        values["enriched_fields"] = _dumps(values.get("enriched_fields") or {})
        values["on_time"] = int(bool(values.get("on_time")))
        values["snapshot_late"] = int(bool(values.get("snapshot_late")))
        _apply_anchor(values, anchor, {**row, "duration_sec": duration_sec}, fetched_at)
        values["unit_mismatch_fields"] = _dumps(values.get("unit_mismatch_fields") or [])
        values["anchor_evidence"] = _dumps(values.get("anchor_evidence") or {})
        placeholders = ", ".join("?" for _ in _INSERT_COLUMNS)
        conn.execute(
            f"INSERT INTO snapshots ({', '.join(_INSERT_COLUMNS)}) VALUES ({placeholders})",
            [values.get(column) for column in _INSERT_COLUMNS],
        )
        conn.commit()
        return FreezeResult(inserted=True)

    enriched: list[str] = []
    updates: dict[str, object] = {}

    for column in ENRICHABLE_COLUMNS:
        new_value = row.get(column)
        if new_value is None or existing[column] is not None:
            continue
        updates[column] = new_value
        enriched.append(column)

    if enriched:
        timestamps = _loads(existing["enriched_fields"], {})
        for column in enriched:
            timestamps[column] = fetched_at
        updates["enriched_fields"] = _dumps(timestamps)

        missing = [f for f in _loads(existing["field_missing"], []) if f not in enriched]
        updates["field_missing"] = _dumps(missing)

        # play_count 是核心三件之一：补齐后 partial 可升 full
        if "play_count" in enriched and existing["completeness"] == "partial":
            updates["completeness"] = "full"

    # 锚点未验证时，后续拿到 duration 允许升级锚点结论（这是校验状态，不是指标值）
    if existing["anchor_unverified"] and anchor.status in ("normal", REPLAY_SUSPECTED, UNIT_MISMATCH):
        anchor_values: dict = {}
        _apply_anchor(anchor_values, anchor, {"duration_sec": duration_sec, **row}, fetched_at)
        updates["anchor_status"] = anchor_values["anchor_status"]
        updates["anchor_unverified"] = anchor_values["anchor_unverified"]
        updates["replay_suspected"] = anchor_values["replay_suspected"]
        updates["unit_mismatch_fields"] = _dumps(anchor_values.get("unit_mismatch_fields") or [])
        updates["anchor_checked_at"] = anchor_values.get("anchor_checked_at")
        updates["anchor_evidence"] = _dumps(anchor_values.get("anchor_evidence") or {})

    updates["last_seen_at"] = max(existing["last_seen_at"] or "", fetched_at)

    assignments = ", ".join(f"{column} = ?" for column in updates)
    conn.execute(
        f"UPDATE snapshots SET {assignments} WHERE video_id = ? AND snapshot_kind = ?",
        [*updates.values(), video_id, kind],
    )
    conn.commit()
    return FreezeResult(inserted=False, enriched=tuple(enriched))


def _select_row(conn: sqlite3.Connection, video_id: str, kind: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM snapshots WHERE video_id = ? AND snapshot_kind = ?",
        (video_id, kind),
    ).fetchone()
