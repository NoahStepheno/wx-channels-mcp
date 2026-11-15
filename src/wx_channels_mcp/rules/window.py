"""快照窗口分桶。

对齐验收基线 acceptance-v1：docs/spec-video-data-access.md §5.3（快照规则）与
§7.2（时间精度），需求见 issue #1。

规则摘要：
- 分钟精度：`d3` = 3 <= offset_days < 4；`d7` = 7 <= offset_days < 8；窗口内 on_time。
- 迟到：offset_days >= target + 1 仍记该 kind，但 late=True（冻结后不改）。
- 早到：offset_days < 3 不标任何 kind（不拿 +2.9 天冒充 +3）。
- 日期精度：按自然日算，d3 = 恰好第 3 天，d7 = 恰好第 7 天，不做小数天。
- 老作品：首次接入时已过 +7 记 backfill，不参与 +3/+7 对比。
"""

from __future__ import annotations

from dataclasses import dataclass

MINUTE = "minute"
DATE = "date"
_PRECISIONS = (MINUTE, DATE)

D3 = "d3"
D7 = "d7"
BACKFILL = "backfill"

# 窗口边界，闭下界、开上界
_D3_START = 3.0
_D7_START = 7.0
_WINDOW_WIDTH = 1.0


@dataclass(frozen=True)
class Bucket:
    """一次抓取的窗口分桶结果。"""

    kind: str | None
    on_time: bool
    late: bool
    offset_days: float
    precision: str


def classify_snapshot(
    offset_days: float,
    *,
    precision: str = MINUTE,
    first_seen: bool = False,
) -> Bucket:
    """把「距发布多久」分到 d3 / d7 / backfill / None。

    Args:
        offset_days: 距发布的天数。`minute` 精度为小数天，`date` 精度为自然日数。
        precision: `minute` 或 `date`；未知值直接报错，不静默降级。
        first_seen: 该作品是否为首次接入（决定是否落 backfill）。
    """
    if precision not in _PRECISIONS:
        raise ValueError(f"未知的时间精度: {precision!r}，只接受 {_PRECISIONS}")
    if isinstance(offset_days, bool):
        raise TypeError("offset_days 必须是数字，不是 bool")
    try:
        offset = float(offset_days)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"offset_days 必须是数字，收到 {offset_days!r}") from exc
    if offset != offset:  # NaN
        raise ValueError("offset_days 不能是 NaN")
    if offset < 0:
        raise ValueError(f"offset_days 不能为负，收到 {offset}")

    if precision == DATE:
        offset = float(int(offset))

    # 首次接入即已过 d7：落 backfill，不参与 +3/+7 对比
    if first_seen and offset >= _D7_START + _WINDOW_WIDTH:
        return Bucket(kind=BACKFILL, on_time=False, late=False, offset_days=offset, precision=precision)

    if offset < _D3_START:
        return Bucket(kind=None, on_time=False, late=False, offset_days=offset, precision=precision)

    if offset < _D7_START:
        kind = D3
        start = _D3_START
    else:
        kind = D7
        start = _D7_START

    on_time = offset < start + _WINDOW_WIDTH
    return Bucket(
        kind=kind,
        on_time=on_time,
        late=not on_time,
        offset_days=offset,
        precision=precision,
    )
