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


def test_300333_with_old_cookie_is_login_required_with_next_action(tmp_path, conn):
    """spec f2dbf0a：过期长得像解析失败，别再让人去查 parser。"""
    from wx_channels_mcp.auth.session import NEXT_ACTION_RELOGIN, SessionState

    state = SessionState(tmp_path / "login_state.json")
    err = LoginRequired("接口返回 errCode=300333 errMsg='request failed'", code=300333)

    summary = sync(FakeApi([], error=err), conn, now=FETCH_AT, session_state=state)

    assert summary.status == "login_required"
    assert summary.error["next_action"] == NEXT_ACTION_RELOGIN
    assert "300333" in summary.error["message"]


def test_300333_after_rescan_still_failing_becomes_parse_failed(tmp_path, conn):
    from datetime import timedelta

    from wx_channels_mcp.auth.session import SessionState

    state = SessionState(tmp_path / "login_state.json")
    err = LoginRequired("接口返回 errCode=300333 errMsg='request failed'", code=300333)
    sync(FakeApi([], error=err), conn, now=FETCH_AT, session_state=state)      # 报过期
    state.note_login(now=FETCH_AT + timedelta(minutes=20))                     # 人扫完了

    summary = sync(FakeApi([], error=err), conn, now=FETCH_AT + timedelta(minutes=21), session_state=state)

    assert summary.status == "parse_failed"
    assert "300333" in summary.error["message"]
    assert "next_action" not in summary.error


def test_300333_without_state_still_login_required(conn):
    err = LoginRequired("接口返回 errCode=300333", code=300333)

    summary = sync(FakeApi([], error=err), conn, now=FETCH_AT)

    assert summary.status == "login_required"
    assert "wx-channels-mcp login" in summary.error["next_action"]


def test_non_300333_parse_failed_has_no_next_action(conn):
    from wx_channels_mcp.fetch.errors import ParseFailed

    summary = sync(FakeApi([], error=ParseFailed("接口返回 errCode=300999", code=300999)), conn, now=FETCH_AT)

    assert summary.status == "parse_failed"
    assert "next_action" not in summary.error


def test_300002_is_parse_failed_with_wrong_account_hint_not_login(conn):
    """v5：号不对（300002）不能把自动化引向"再扫一次"。"""
    from wx_channels_mcp.fetch.client import build_parse_failed

    err = build_parse_failed(300002, "request failed", detail="{}")
    summary = sync(FakeApi([], error=err), conn, now=FETCH_AT)

    assert summary.status == "parse_failed"
    assert "next_action" not in summary.error
    assert "登录账号不对" in summary.error["message"]


STORED_FIELDS = (
    "offset_days",
    "on_time",
    "snapshot_late",
    "play_count",
    "finish_rate",
    "avg_watch_sec",
    "fetched_at",
    "last_seen_at",
)


def test_preview_distinguishes_fetched_from_stored(conn):
    """issue #4：JSON 顶层是本次抓取值，stored 必须是落库后的真实行。"""
    first = sync(FakeApi(FIXTURE_POSTS), conn, now=FETCH_AT)
    row = first.snapshots[0]

    assert set(STORED_FIELDS) <= set(row["stored"])
    assert row["stored"]["play_count"] == 574
    assert row["stored"]["fetched_at"] == row["stored"]["last_seen_at"]
    assert row["stored"]["offset_days"] == pytest.approx(row["offset_days"], abs=1e-3)

    # 重跑：平台播放量涨到 999，但 d3 快照已冻结 → fetched 变、stored 不变
    post = json.loads(json.dumps(FIXTURE_POSTS[0]))
    post["readCount"] = 999
    later = datetime(2026, 11, 12, 19, 0, tzinfo=timezone.utc)
    second = sync(FakeApi([post]), conn, now=later)
    row = second.snapshots[0]

    assert row["inserted"] is False
    assert row["play_count"] == 999, "顶层是本次抓取值"
    assert row["stored"]["play_count"] == 574, "stored 是冻结后的库内值"
    assert row["stored"]["offset_days"] == pytest.approx(3.0606, abs=1e-3)
    assert row["stored"]["last_seen_at"] > row["stored"]["fetched_at"]
    assert row["stored"]["on_time"] is True


def test_preview_dry_run_has_no_stored_row(conn):
    summary = sync(FakeApi(FIXTURE_POSTS), conn, dry_run=True, now=FETCH_AT)
    row = summary.snapshots[0]
    assert row["stored"] is None
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
