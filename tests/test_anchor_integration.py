"""锚点 B 接进落库路径后的行为（acceptance-v4 洞 2）。

- `insufficient_data`（缺 duration）**不算通过**：`anchor_unverified=1`
- `unit_mismatch`：**行仍落库**、字段标疑，不是整行丢弃
- `replay_suspected`：不停字段，但标 `replay_suspected=1` 且不进 H1
- 后续拿到 duration 允许把"未验证"升级为确定结论（这是校验状态，不是指标值）
"""

import sqlite3

import pytest

from wx_channels_mcp.rules.baseline import is_baseline_eligible, offsets_comparable
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
        completeness="full",
        field_missing=[],
        parser_version="0.1.0",
    )
    base.update(overrides)
    return base


def test_missing_duration_leaves_anchor_unverified(conn):
    freeze_snapshot(conn, _row())  # 没有 duration_sec
    stored = get_snapshot(conn, "export/vid-1", "d3")

    assert stored["anchor_status"] == "insufficient_data"
    assert stored["anchor_unverified"] == 1
    assert is_baseline_eligible(stored) is False


def test_normal_anchor_marks_trusted(conn):
    freeze_snapshot(conn, _row(duration_sec=60.0))
    stored = get_snapshot(conn, "export/vid-1", "d3")

    assert stored["anchor_status"] == "normal"
    assert stored["anchor_unverified"] == 0
    assert is_baseline_eligible(stored) is True


def test_unit_mismatch_keeps_row_but_flags_fields(conn):
    # 均播 60000s、时长 60s：只能是单位错
    freeze_snapshot(conn, _row(avg_watch_sec=60000.0, duration_sec=60.0))
    stored = get_snapshot(conn, "export/vid-1", "d3")

    assert stored is not None, "行必须落库，不许整行丢弃"
    assert stored["anchor_status"] == "unit_mismatch"
    assert stored["anchor_unverified"] == 1
    assert "avg_watch_sec" in stored["unit_mismatch_fields"]
    assert is_baseline_eligible(stored) is False


def test_replay_suspected_passes_anchor_but_blocked_from_baseline(conn):
    freeze_snapshot(conn, _row(avg_watch_sec=75.0, duration_sec=60.0))
    stored = get_snapshot(conn, "export/vid-1", "d3")

    assert stored["anchor_status"] == "replay_suspected"
    assert stored["anchor_unverified"] == 0
    assert stored["replay_suspected"] == 1
    assert is_baseline_eligible(stored) is False, "含重播口径不能进 H1"


def test_late_snapshot_never_enters_baseline(conn):
    freeze_snapshot(conn, _row(offset_days=6.99, on_time=False, snapshot_late=True, duration_sec=60.0))
    stored = get_snapshot(conn, "export/vid-1", "d3")

    assert stored["snapshot_late"] == 1
    assert is_baseline_eligible(stored) is False


def test_anchor_upgrades_when_duration_arrives_later(conn):
    freeze_snapshot(conn, _row())  # 第一次没有 duration
    freeze_snapshot(conn, _row(duration_sec=60.0, fetched_at="2026-11-12T11:00:00+00:00"))
    stored = get_snapshot(conn, "export/vid-1", "d3")

    assert stored["anchor_status"] == "normal"
    assert stored["anchor_unverified"] == 0


def test_offsets_comparable_within_half_day():
    assert offsets_comparable(3.0, 3.4) is True
    assert offsets_comparable(3.0, 3.6) is False
    assert offsets_comparable(3.0, 6.99) is False
    assert offsets_comparable(None, 3.0) is False
