"""冻结语义测试（acceptance-v4 §5.3 / §7.1 幂等）。

规矩：
- 唯一键 (video_id, snapshot_kind)；首次成功写入即冻结。
- 重跑只推进 last_seen_at / fetched_at，**不覆盖已冻结的数值**。
- 已冻结行只允许补空字段（补数记 enriched_fields 字段级时点）。
- play_count = 0 是有效值，能冻结、completeness = full。
"""

import sqlite3

import pytest

from wx_channels_mcp.storage.db import init_db
from wx_channels_mcp.storage.snapshots import freeze_snapshot, get_snapshot


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def _row(**overrides):
    base = dict(
        video_id="export/vid-1",
        snapshot_kind="d3",
        fetched_at="2026-11-12T10:00:00+00:00",
        offset_days=3.0,
        on_time=True,
        snapshot_late=False,
        play_count=574,
        finish_rate=0.0592,
        avg_watch_sec=23.79,
        like_count=9,
        comment_count=10,
        share_count=10,
        favorite_count=11,
        follower_delta=8,
        period=None,
        rate_unit="ratio",
        completeness="full",
        field_missing=[],
        parser_version="0.1.0",
    )
    base.update(overrides)
    return base


def test_first_freeze_writes_row(conn):
    result = freeze_snapshot(conn, _row())

    assert result.inserted is True
    stored = get_snapshot(conn, "export/vid-1", "d3")
    assert stored["play_count"] == 574
    assert stored["completeness"] == "full"


def test_second_freeze_does_not_overwrite_metrics(conn):
    freeze_snapshot(conn, _row())
    again = freeze_snapshot(
        conn,
        _row(play_count=999, like_count=100, fetched_at="2026-11-12T12:00:00+00:00"),
    )

    assert again.inserted is False
    stored = get_snapshot(conn, "export/vid-1", "d3")
    assert stored["play_count"] == 574, "已冻结的数值不得被覆盖"
    assert stored["like_count"] == 9
    assert stored["last_seen_at"] == "2026-11-12T12:00:00+00:00"


def test_missing_fields_are_filled_and_timestamped(conn):
    freeze_snapshot(conn, _row(avg_watch_sec=None, field_missing=["avg_watch_sec"]))
    freeze_snapshot(conn, _row(fetched_at="2026-11-12T15:00:00+00:00", avg_watch_sec=24.5))

    stored = get_snapshot(conn, "export/vid-1", "d3")
    assert stored["avg_watch_sec"] == 24.5, "空字段允许补数"
    enriched = stored["enriched_fields"]
    assert "avg_watch_sec" in enriched
    assert enriched["avg_watch_sec"] == "2026-11-12T15:00:00+00:00"


def test_non_null_frozen_values_never_change_on_enrich(conn):
    freeze_snapshot(conn, _row(finish_rate=0.05))
    freeze_snapshot(conn, _row(fetched_at="2026-11-12T16:00:00+00:00", finish_rate=0.09))

    stored = get_snapshot(conn, "export/vid-1", "d3")
    assert stored["finish_rate"] == 0.05
    assert "finish_rate" not in (stored["enriched_fields"] or {})


def test_zero_play_count_freezes_as_full(conn):
    result = freeze_snapshot(conn, _row(play_count=0))

    assert result.inserted is True
    stored = get_snapshot(conn, "export/vid-1", "d3")
    assert stored["play_count"] == 0
    assert stored["completeness"] == "full"


def test_late_flag_is_frozen_too(conn):
    freeze_snapshot(conn, _row(snapshot_late=True, on_time=False, offset_days=4.2))
    freeze_snapshot(conn, _row(offset_days=3.1, on_time=True, snapshot_late=False))

    stored = get_snapshot(conn, "export/vid-1", "d3")
    assert stored["snapshot_late"] == 1
    assert stored["on_time"] == 0
    assert stored["offset_days"] == 4.2
