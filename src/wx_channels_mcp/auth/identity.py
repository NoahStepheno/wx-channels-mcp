r"""登录身份解析（spec 6.1.1 / v6）：login 必须能说出"登进去的是哪个号"。

来源只读一次：
- 昵称/视频号ID：登录页 `inner_text`（不新增登录、不导出 cookie）；
- 视频数：**只**来自 `post_list` 的 `totalCount`。

页面文本里绝不猜视频数：事故页面有「视频号ID: …」和「关注者0」，用 `视频\s*(\d+)`
会把 0 作品的号解析成 28——编数比不报严重得多（2026-11-16 回声验收）。

任一项拿不到都降级为 `identity: null` + `identity_unverified: true`，绝不让登录失败；
探测结果用 `probe` 明说：`ok` / `denied`(300002，很可能扫错号) / `error`。
"""

from __future__ import annotations

import re

from wx_channels_mcp.fetch.client import WRONG_ACCOUNT_CODE
from wx_channels_mcp.fetch.errors import WxError

# spec v6.5：昵称靠**结构锚点**认，不靠黑名单。
# 锚点：`助手 | [序号 |] 昵称 | [申请认证 |] 视频号ID`——真机天然满足，菜单列表天然不满足。
_NICKNAME = re.compile(
    r"助手\s*[|｜]\s*(?:\d+\s*[|｜]\s*)?([^|｜\n]{1,30}?)\s*[|｜]\s*(?:申请认证\s*[|｜]\s*)?视频号ID"
)
_FINDER_ID = re.compile(r"视频号ID\s*[:：]?\s*([A-Za-z0-9_@\-]{6,})")

# 黑名单只做辅助，且只留实测漏网的那种"英文设置页标签"
_NICKNAME_DENYLIST = {
    # 英文设置页标签（实测漏网）
    "settings", "settings…", "preferences",
    # 结构段本身：昵称后面允许跟 `申请认证 |`，所以它自己绝不可能是昵称
    "申请认证", "关注者", "视频号id",
}

PROBE_OK = "ok"
PROBE_DENIED = "denied"
PROBE_ERROR = "error"


def _is_denied_nickname(candidate: str) -> bool:
    return candidate.strip().lower() in _NICKNAME_DENYLIST


def parse_identity(page_text: str) -> dict | None:
    """从登录页可见文本只解析 `nickname`/`finder_id`；视频数一律不从这里拿。"""
    text = (page_text or "").strip()
    if not text:
        return None

    nickname_match = _NICKNAME.search(text)
    candidate = nickname_match.group(1).strip() if nickname_match else ""
    if not candidate or _is_denied_nickname(candidate):
        return None

    # 昵称只用于展示：这里拿不到不影响 probe/status（v6.5）
    identity: dict[str, object] = {"nickname": candidate}
    finder_match = _FINDER_ID.search(text)
    if finder_match:
        identity["finder_id"] = finder_match.group(1)
    return identity


def probe_video_count(api) -> tuple[str, int | None]:
    """一次最小 `post_list` 读 `totalCount`，并保留失败原因。

    返回 `(probe, video_count)`：`ok` 拿到数、`denied` 是 300002（号不对）、其余 `error`。
    """
    try:
        data = api.list_posts(page_size=1)
    except WxError as exc:
        if getattr(exc, "code", None) == WRONG_ACCOUNT_CODE:
            return PROBE_DENIED, None
        return PROBE_ERROR, None
    total = (data.get("data") or {}).get("totalCount")
    try:
        return PROBE_OK, int(total)
    except (TypeError, ValueError):
        return PROBE_ERROR, None
