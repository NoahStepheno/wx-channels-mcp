"""基准线的可比性判定（acceptance-v4 洞 1）。

`late` 快照照常落库、照常可查，但**默认不进基准线与任何跨条对比**：
6.99 天累计的播放量和 3.1 天不是同一个东西。真要拿 late 救样本，
必须同行记 `offset_days`，且只与相差 ≤ 0.5 天的比。
"""

from __future__ import annotations

LATE_OFFSET_TOLERANCE_DAYS = 0.5


def is_baseline_eligible(snapshot: dict) -> bool:
    """能否进入基准线/跨条对比。"""
    if not snapshot.get("on_time"):
        return False
    if snapshot.get("snapshot_late"):
        return False
    if snapshot.get("replay_suspected"):
        return False
    if snapshot.get("anchor_unverified"):
        return False
    if snapshot.get("anchor_status") == "unit_mismatch":
        return False
    return True


def offsets_comparable(a_offset_days: float, b_offset_days: float) -> bool:
    """两条快照的 offset 是否近到可以互相比。"""
    if a_offset_days is None or b_offset_days is None:
        return False
    return abs(float(a_offset_days) - float(b_offset_days)) <= LATE_OFFSET_TOLERANCE_DAYS
