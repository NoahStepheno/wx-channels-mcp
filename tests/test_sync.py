"""sync 全链路：dry-run 不写库、重跑不改冻结值、play_count=0、错误分桶。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from wx_channels_mcp.fetch.errors import LoginRequired
from wx_channels_mcp.services.normalize import to_metrics, to_video
from wx_channels_mcp.services.sync import sync
from wx_channels_mcp.storage.db import init_db
from wx_channels_mcp.storage.snapshots import get_snapshot

FIXTURE = Path(__file__).with_name("fixtures_post_list_sample.json")
FIXTURE_POSTS = json.loads(FIXTURE.read_text())["list"]


class FakeApi:
    def __init__(self, posts, error: Exception | None = None):
        self.posts = posts
        self.error = error
        self.calls: list[dict] = []

    def list_posts(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"errCode": 0, "data": {"list": self.posts}}


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


# 样例作品发布于 2026-11-09 13:32 UTC；下面用 +3.06 天当抓取时间
PUBLISHED = datetime(2026, 11, 9, 13, 32, 28, tzinfo=timezone.utc)
FETCH_AT = datetime(2026, 11, 12, 15, 0, 0, tzinfo=timezone.utc)


def test_normalize_keeps_ratio_units_and_duration():
    video = to_video(FIXTURE_POSTS[0])
    metrics = to_metrics(FIXTURE_POSTS[0])

    assert video.video_id.startswith("export/")
    assert video.published_raw == 1794231148
    assert video.time_precision == "minute"
    assert metrics["play_count"] == 574
    assert 0 < metrics["finish_rate"] < 1, "完播率保持 0-1 小数，不乘 100"
    assert 0 < metrics["fast_flip_rate"] < 1


def test_dry_run_does_not_write(conn):
    api = FakeApi(FIXTURE_POSTS)
    summary = sync(api, conn, dry_run=True, now=FETCH_AT)

    assert summary.dry_run is True
    assert len(summary.snapshots) == 1
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0


def test_sync_passes_server_time_range(conn):
    api = FakeApi(FIXTURE_POSTS)
    sync(api, conn, dry_run=True, now=FETCH_AT, lookback_days=30)

    call = api.calls[0]
    assert call["start_time"] < call["end_time"]
    # 30 天窗口内；服务端区间与本地分桶口径一致，行数为 1
    assert call["end_time"] - call["start_time"] == 30 * 86400


def test_real_sync_freezes_and_rerun_keeps_values(conn):
    api = FakeApi(FIXTURE_POSTS)
    first = sync(api, conn, now=FETCH_AT)

    assert first.snapshots[0]["snapshot_kind"] == "d3"
    assert first.snapshots[0]["inserted"] is True
    stored = get_snapshot(conn, FIXTURE_POSTS[0]["objectId"], "d3")
    assert stored["play_count"] == 574

    # 重跑（+3.2 天仍在窗口内）：不新增行、数值不变
    second = sync(FakeApi(FIXTURE_POSTS), conn, now=datetime(2026, 11, 12, 19, 0, tzinfo=timezone.utc))
    assert second.snapshots[0]["inserted"] is False
    again = get_snapshot(conn, FIXTURE_POSTS[0]["objectId"], "d3")
    assert again["play_count"] == 574
    assert again["last_seen_at"] > stored["last_seen_at"]


def test_play_count_zero_freezes_as_full(conn):
    post = json.loads(json.dumps(FIXTURE_POSTS[0]))
    post["readCount"] = 0
    summary = sync(FakeApi([post]), conn, now=FETCH_AT)

    stored = get_snapshot(conn, post["objectId"], "d3")
    assert summary.snapshots[0]["inserted"] is True
    assert stored["play_count"] == 0
    assert stored["completeness"] == "full"


def test_out_of_window_is_skipped_not_backfilled(conn):
    summary = sync(FakeApi(FIXTURE_POSTS), conn, now=datetime(2026, 11, 12, 5, 0, tzinfo=timezone.utc))

    assert summary.snapshots == []
    assert summary.skipped[0]["reason"] == "not_in_window"
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0


def test_login_required_is_reported_not_raised(conn):
    summary = sync(FakeApi([], error=LoginRequired("登录失效")), conn, now=FETCH_AT)

    assert summary.status == "login_required"
    assert summary.error["reason"] == "login_required"
    assert "登录失效" in summary.error["message"]


def test_json_summary_written(tmp_path, conn):
    path = tmp_path / "run.json"
    sync(FakeApi(FIXTURE_POSTS), conn, dry_run=True, now=FETCH_AT, json_path=path)

    data = json.loads(path.read_text())
    assert data["status"] == "ok"
    assert data["snapshots"][0]["snapshot_kind"] == "d3"
    assert data["dry_run"] is True


def test_first_seen_old_post_becomes_backfill(conn):
    # 首次接入即已过 +7：记 backfill，不参与 +3/+7 对比
    summary = sync(FakeApi(FIXTURE_POSTS), conn, now=datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc))

    assert summary.snapshots[0]["snapshot_kind"] == "backfill"
    stored = get_snapshot(conn, FIXTURE_POSTS[0]["objectId"], "backfill")
    assert stored["on_time"] == 0


def test_missing_field_is_counted_across_batches(conn):
    post = json.loads(json.dumps(FIXTURE_POSTS[0]))
    post["favCount"] = None

    sync(FakeApi([post]), conn, now=FETCH_AT)
    rows = conn.execute("SELECT * FROM field_absence").fetchall()
    assert [r["field"] for r in rows] == ["favorite_count"]
    assert rows[0]["consecutive_missing"] == 1
