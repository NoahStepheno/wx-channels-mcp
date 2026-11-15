"""锚点 B：播放指标内部一致性校验（acceptance-v4）。

判据（回声 cc58eae 修正后三档）：
- `avg <= duration * 1.05`       -> normal
- `1.05 < avg <= duration * 1.5` -> replay_suspected（重播，合法，但口径含重播，不进 H1）
- `avg > duration * 1.5`         -> unit_mismatch（基本只能是单位错，例如时长是毫秒）

附加恒真约束：`full_play_rate <= avg/duration + 容差`。
看完的人必然贡献满时长，所以完播率不可能超过「均播/时长」；违反即有字段错。
"""

from __future__ import annotations

from dataclasses import dataclass

NORMAL = "normal"
REPLAY_SUSPECTED = "replay_suspected"
UNIT_MISMATCH = "unit_mismatch"
INSUFFICIENT = "insufficient_data"

NORMAL_LIMIT = 1.05
REPLAY_LIMIT = 1.5
FINISH_RATE_TOLERANCE = 0.02


@dataclass(frozen=True)
class Consistency:
    status: str
    stop_field: bool
    replay_suspected: bool
    reason: str = ""


def check_play_consistency(
    avg_play_sec: float | None,
    duration_sec: float | None,
    full_play_rate: float | None = None,
) -> Consistency:
    """校验均播/时长/完播率三者是否自洽。"""
    if not avg_play_sec or not duration_sec or duration_sec <= 0:
        return Consistency(INSUFFICIENT, stop_field=False, replay_suspected=False)

    ratio = avg_play_sec / duration_sec

    if ratio > REPLAY_LIMIT:
        return Consistency(
            UNIT_MISMATCH,
            stop_field=True,
            replay_suspected=False,
            reason=f"avg/duration={ratio:.2f} > {REPLAY_LIMIT}（疑似单位错误）",
        )

    if full_play_rate is not None and full_play_rate > ratio + FINISH_RATE_TOLERANCE:
        return Consistency(
            UNIT_MISMATCH,
            stop_field=True,
            replay_suspected=False,
            reason=(
                f"full_play_rate={full_play_rate:.3f} > avg/duration={ratio:.3f}"
                f" + {FINISH_RATE_TOLERANCE}（完播率与均播矛盾）"
            ),
        )

    if ratio > NORMAL_LIMIT:
        return Consistency(
            REPLAY_SUSPECTED,
            stop_field=False,
            replay_suspected=True,
            reason=f"avg/duration={ratio:.2f} 在重播区间，口径含重播，不进 H1",
        )

    return Consistency(NORMAL, stop_field=False, replay_suspected=False)
