"""字段级缺失计数：3 次 + 跨 2 批次才升 unsupported，读到即清零。"""

import sqlite3

import pytest

from wx_channels_mcp.services.absence import get_absence, record_absence, record_present
from wx_channels_mcp.storage.db import init_db


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def test_three_times_across_two_batches_upgrades_to_unsupported(conn):
    record_absence(conn, "vid", ["fav_count"], batch_id="b1", checked_at="t1")
    record_absence(conn, "vid", ["fav_count"], batch_id="b1", checked_at="t2")
    upgraded = record_absence(conn, "vid", ["fav_count"], batch_id="b1", checked_at="t3")

    assert upgraded == [], "同一批次重试不算跨批次，不得升级"
    row = get_absence(conn, "vid")[0]
    assert row["consecutive_missing"] == 3
    assert row["status"] == "counting"

    upgraded = record_absence(conn, "vid", ["fav_count"], batch_id="b2", checked_at="t4")
    assert upgraded == ["fav_count"]
    row = get_absence(conn, "vid")[0]
    assert row["status"] == "unsupported"
    assert row["batches_seen"] == ["b1", "b2"]


def test_reading_field_again_resets_counter(conn):
    record_absence(conn, "vid", ["fav_count"], batch_id="b1", checked_at="t1")
    record_absence(conn, "vid", ["fav_count"], batch_id="b2", checked_at="t2")
    record_present(conn, "vid", ["fav_count"], checked_at="t3")

    row = get_absence(conn, "vid")[0]
    assert row["consecutive_missing"] == 0
    assert row["status"] == "counting"


def test_present_without_history_creates_no_row(conn):
    record_present(conn, "vid", ["play_count"], checked_at="t1")

    assert get_absence(conn, "vid") == []


def test_first_missing_records_first_seen_time(conn):
    record_absence(conn, "vid", ["comment_count"], batch_id="b1", checked_at="2026-11-15T10:00:00Z")

    row = get_absence(conn, "vid")[0]
    assert row["first_missing_at"] == "2026-11-15T10:00:00Z"
    assert row["last_checked_at"] == "2026-11-15T10:00:00Z"
