"""基准线与对比的准入判定（acceptance-v4 / 回声 4f3cd26）。

两条判据分开，别合成一条：

- `is_baseline_eligible`（进基准线）：**允许** `replay_suspected=1`。
  重播是结果不是误差，重播作品往往正是表现最好的样本；整体踢掉会把基准线压低。
  仍要求：`on_time=1`、非 late、锚点通过、非 `unit_mismatch`。
  基准线统计需同时报「含重播 n / 不含重播 n」。
- `is_compare_eligible`（A/C 对比、跨条排名）：在基准线之上再排除 `replay_suspected=1`，
  因为重播量不同会让留存类指标不可比。
"""

from __future__ import annotations

LATE_OFFSET_TOLERANCE_DAYS = 0.5


def is_baseline_eligible(snapshot: dict) -> bool:
    """能否进入基准线（允许重播样本）。"""
    if not snapshot.get("on_time"):
        return False
    if snapshot.get("snapshot_late"):
        return False
    if snapshot.get("anchor_unverified"):
        return False
    if snapshot.get("anchor_status") == "unit_mismatch":
        return False
    return True


def is_compare_eligible(snapshot: dict) -> bool:
    """能否进入 A/C 对比与跨条排名（重播样本不可比）。"""
    if not is_baseline_eligible(snapshot):
        return False
    return not snapshot.get("replay_suspected")


def offsets_comparable(a_offset_days: float, b_offset_days: float) -> bool:
    """两条快照的 offset 是否近到可以互相比。"""
    if a_offset_days is None or b_offset_days is None:
        return False
    return abs(float(a_offset_days) - float(b_offset_days)) <= LATE_OFFSET_TOLERANCE_DAYS
