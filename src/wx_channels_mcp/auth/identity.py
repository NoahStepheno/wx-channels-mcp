"""登录身份解析（spec 6.1.1）：login 必须能说出"登进去的是哪个号"。

来源只读一次：
- 昵称/视频号ID：登录页 `inner_text`（不新增登录、不导出 cookie）；
- 视频数：`post_list` 的 `totalCount`（比页面文本更可信，且顺带暴露"号不对 → 300002"）。
任一项拿不到都降级为 `identity: null` + `identity_unverified: true`，绝不让登录失败。
"""

from __future__ import annotations

import re

from wx_channels_mcp.fetch.errors import WxError

_NICKNAME = re.compile(r"助手\s*[|｜]\s*\d+\s*[|｜]\s*([^|｜\n]{1,40}?)\s*[|｜]")
_VIDEO_COUNT = re.compile(r"视频\s*(\d+)")
_FINDER_ID = re.compile(r"视频号ID\s*[:：]?\s*([A-Za-z0-9_@\-]{6,})")


def parse_identity(page_text: str) -> dict | None:
    """从登录页可见文本解析身份；缺 `nickname` 或 `video_count` 就返回 None。"""
    text = (page_text or "").strip()
    if not text:
        return None

    nickname_match = _NICKNAME.search(text)
    count_match = _VIDEO_COUNT.search(text)
    if not nickname_match or not count_match:
        return None

    identity: dict[str, object] = {
        "nickname": nickname_match.group(1).strip(),
        "video_count": int(count_match.group(1)),
    }
    finder_match = _FINDER_ID.search(text)
    if finder_match:
        identity["finder_id"] = finder_match.group(1)
    return identity


def fetch_video_count(api) -> int | None:
    """用一次最小 `post_list` 读 `totalCount`；任何失败都返回 None（不阻塞登录）。"""
    try:
        data = api.list_posts(page_size=1)
    except WxError:
        return None
    total = (data.get("data") or {}).get("totalCount")
    try:
        return int(total)
    except (TypeError, ValueError):
        return None
