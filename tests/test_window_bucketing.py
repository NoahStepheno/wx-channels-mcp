"""窗口分桶与幂等相关的行为测试。

对齐验收基线：acceptance-v1（docs/spec-video-data-access.md §5.3 / §5.4 / §7.2）
issue: #1
"""

import pytest

from wx_channels_mcp.rules.window import Bucket, classify_snapshot


# --- 分钟精度：主窗口边界 -------------------------------------------------


@pytest.mark.parametrize(
    ("offset_days", "kind", "on_time", "late"),
    [
        (0.0, None, False, False),      # 刚发布，还没到窗口
        (2.999, None, False, False),    # 2.9 系：不得标 d3
        (3.0, "d3", True, False),       # 窗口下界，含
        (3.5, "d3", True, False),
        (3.999, "d3", True, False),     # 窗口上界，不含 4.0
        (4.0, "d3", False, True),       # 迟到：仍记 d3，但 late
        (6.5, "d3", False, True),       # 落在 d3 之后、d7 之前
        (7.0, "d7", True, False),
        (7.5, "d7", True, False),
        (7.999, "d7", True, False),
        (8.0, "d7", False, True),
    ],
)
def test_minute_precision_buckets(offset_days, kind, on_time, late):
    bucket = classify_snapshot(offset_days, precision="minute")

    assert isinstance(bucket, Bucket)
    assert bucket.kind == kind
    assert bucket.on_time is on_time
    assert bucket.late is late


def test_negative_offset_is_rejected():
    with pytest.raises(ValueError):
        classify_snapshot(-0.5, precision="minute")


# --- 日期精度：按自然日，不做小数天 ---------------------------------------


@pytest.mark.parametrize(
    ("day_offset", "kind", "on_time", "late"),
    [
        (0, None, False, False),
        (2, None, False, False),
        (3, "d3", True, False),
        (4, "d3", False, True),
        (6, "d3", False, True),
        (7, "d7", True, False),
        (8, "d7", False, True),
    ],
)
def test_date_precision_uses_whole_days(day_offset, kind, on_time, late):
    bucket = classify_snapshot(day_offset, precision="date")

    assert bucket.kind == kind
    assert bucket.on_time is on_time
    assert bucket.late is late
    assert bucket.offset_days == float(day_offset)


def test_unknown_precision_is_rejected():
    with pytest.raises(ValueError):
        classify_snapshot(3.0, precision="week")


# --- 老作品：首次接入已过 +7，记 backfill，不参与 +3/+7 对比 ----------------


@pytest.mark.parametrize("offset_days", [8.0, 15.2, 90.0])
def test_first_seen_after_d7_is_backfill(offset_days):
    bucket = classify_snapshot(offset_days, precision="minute", first_seen=True)

    assert bucket.kind == "backfill"
    assert bucket.on_time is False
    assert bucket.late is False


def test_first_seen_inside_window_still_uses_real_window():
    bucket = classify_snapshot(3.4, precision="minute", first_seen=True)

    assert bucket.kind == "d3"
    assert bucket.on_time is True
