"""本地 CSV 导出：表头固定，能被复盘记录表映射。"""

import csv
import sqlite3

import pytest

from wx_channels_mcp.services.export import COLUMNS, export_csv, export_rows
from wx_channels_mcp.storage.db import init_db


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    c.execute(
        "INSERT INTO videos (video_id, account_id, published_at, duration_sec, first_seen_at, last_seen_at)"
        " VALUES ('vid1', 'acc', '2026-11-11T10:00:00+00:00', 162, 'x', 'x')"
    )
    c.execute(
        "INSERT INTO snapshots (video_id, snapshot_kind, fetched_at, last_seen_at, offset_days,"
        " on_time, snapshot_late, play_count, finish_rate, completeness, parser_version, rate_unit)"
        " VALUES ('vid1', 'd3', '2026-11-14T10:00:00+00:00', '2026-11-14T10:00:00+00:00',"
        " 3.0, 1, 0, 574, 0.0592, 'full', '0.1.0', 'ratio')"
    )
    c.commit()
    yield c
    c.close()


def test_csv_has_stable_columns_and_row(conn, tmp_path):
    out = tmp_path / "snapshots.csv"
    rows = export_csv(conn, out)

    assert rows == 1
    with out.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames) == COLUMNS
        record = next(reader)
    assert record["video_id"] == "vid1"
    assert record["snapshot_kind"] == "d3"
    assert record["play_count"] == "574"
    assert record["finish_rate"].startswith("0.0592"), "比率导出保持 0-1 小数"


def test_review_table_columns_present(conn, tmp_path):
    out = tmp_path / "snapshots.csv"
    export_csv(conn, out)
    with out.open(encoding="utf-8") as handle:
        record = next(csv.DictReader(handle))

    # 3 秒留存相关的三列必须在，空也要能解释
    assert "fast_flip_rate" in record
    assert "keep_rate_3s" in record
    assert "pending_unit_check" in record
    assert record["keep_rate_3s"] == ""
    assert record["published_date_local"] == "2026-11-11", "10:00Z = 当地 18:00，仍是同一天"
    assert record["duration_sec"] == "162", "复盘要用时长判断节奏，缺失就得回库 join"


def test_since_filter(conn):
    assert len(export_rows(conn, since="2026-11-01")) == 1
    assert export_rows(conn, since="2026-12-01") == []
