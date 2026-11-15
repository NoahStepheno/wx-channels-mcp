"""锚点 B：跨字段内部一致性（acceptance-v4 §8）。

三档（回声 cc58eae 修正）：
- avg <= duration * 1.05            -> normal
- 1.05 < avg <= 1.5 * duration      -> replay_suspected（重播，不停字段，但不进 H1）
- avg > 1.5 * duration              -> unit_mismatch（停字段，多半是单位错，如毫秒当时长）

另：fullPlayRate <= avg/duration + 容差 恒真，违反即 unit_mismatch。
"""

import pytest

from wx_channels_mcp.rules.anchors import check_play_consistency


@pytest.mark.parametrize(
    ("avg", "duration", "status"),
    [
        (23.79, 60.0, "normal"),
        (60.0, 60.0, "normal"),
        (63.0, 60.0, "normal"),          # 正好 1.05 倍，仍算正常
        (63.1, 60.0, "replay_suspected"),  # 刚过 1.05
        (90.0, 60.0, "replay_suspected"),  # 正好 1.5 倍
        (90.1, 60.0, "unit_mismatch"),     # 超过 1.5 倍
        (60000.0, 60.0, "unit_mismatch"),  # 毫秒当时长的典型错法
    ],
)
def test_three_band_play_consistency(avg, duration, status):
    result = check_play_consistency(avg_play_sec=avg, duration_sec=duration)

    assert result.status == status
    assert result.stop_field is (status == "unit_mismatch")


def test_replay_suspected_is_flagged_but_kept():
    result = check_play_consistency(avg_play_sec=75.0, duration_sec=60.0)

    assert result.status == "replay_suspected"
    assert result.replay_suspected is True
    assert result.stop_field is False


def test_full_play_rate_cannot_exceed_watch_ratio():
    # 看完的人必然贡献满时长：完播率 0.9 而均播只有时长的一半 -> 必有一方错
    result = check_play_consistency(avg_play_sec=30.0, duration_sec=60.0, full_play_rate=0.9)

    assert result.status == "unit_mismatch"
    assert result.stop_field is True
    assert "full_play_rate" in result.reason


def test_full_play_rate_within_tolerance_is_fine():
    result = check_play_consistency(avg_play_sec=30.0, duration_sec=60.0, full_play_rate=0.51)

    assert result.status == "normal"


def test_finished_rate_boundary_is_inclusive():
    result = check_play_consistency(avg_play_sec=30.0, duration_sec=60.0, full_play_rate=0.5)

    assert result.status == "normal"


@pytest.mark.parametrize(
    ("avg", "duration"),
    [(None, 60.0), (30.0, None), (None, None), (0.0, 60.0)],
)
def test_missing_or_zero_inputs_are_insufficient(avg, duration):
    result = check_play_consistency(avg_play_sec=avg, duration_sec=duration)

    assert result.status == "insufficient_data"
    assert result.stop_field is False
