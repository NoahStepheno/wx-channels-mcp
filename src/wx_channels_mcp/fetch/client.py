"""HTTP 取数层：只吃 cookie + 参数，不依赖 Playwright 生命周期。

失败分类（acceptance-v4 / spec f2dbf0a）：
- HTTP 429                 -> rate_limited（可退避）
- HTTP 401/403             -> login_required
- 返回体是 HTML/非 JSON     -> login_required（登录页）或 parse_failed
- 业务 errCode=300333      -> login_required（登录态过期；降级判定在 sync 层，靠登录时间线）
- 其它业务 errCode != 0    -> parse_failed（保留原始 errCode/errMsg）
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from wx_channels_mcp.fetch.errors import LoginRequired, ParseFailed, RateLimited

# 300333：cookie 过期。语义上等于"重新扫码"，不等于"parser 不会解析"
EXPIRED_SESSION_CODE = 300333

BASE_URL = "https://channels.weixin.qq.com"
POST_LIST_PATH = "/cgi-bin/mmfinderassistant-bin/post/post_list"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
)


def business_error_reason(err_code: int | None) -> str | None:
    """业务 errCode -> 失败原因；None 表示成功。"""
    if err_code in (0, None):
        return None
    return "login_required" if err_code == EXPIRED_SESSION_CODE else "parse_failed"


def classify_failure(status: int, content_type: str, text: str) -> str | None:
    """把一次响应分类成失败原因；返回 None 表示可以继续解析 JSON。"""
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "login_required"

    body = (text or "").lstrip()
    looks_html = "text/html" in (content_type or "").lower() or body.startswith("<")
    if looks_html:
        lowered = body[:4000].lower()
        return "login_required" if ("login" in lowered or "扫码" in lowered or "登录" in lowered) else "parse_failed"
    if status >= 400:
        return "parse_failed"
    return None


def _raise(reason: str, *, status: int, text: str) -> None:
    snippet = (text or "")[:200].replace("\n", " ")
    if reason == "rate_limited":
        raise RateLimited(f"HTTP {status} 被限速/风控", detail=snippet)
    if reason == "login_required":
        raise LoginRequired(f"登录态失效（HTTP {status}）", detail=snippet)
    raise ParseFailed(f"响应无法解析（HTTP {status}）", detail=snippet)


class WxApi:
    """视频号助手内部接口客户端（只读）。"""

    def __init__(self, cookies: dict[str, str], *, base_url: str = BASE_URL, timeout: float = 30.0) -> None:
        self._cookies = dict(cookies)
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    # --- 底层 -----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Cookie": "; ".join(f"{k}={v}" for k, v in self._cookies.items()),
            "Referer": f"{self._base_url}/platform/statistic/post",
            "User-Agent": USER_AGENT,
        }

    def post_json(self, path: str, payload: dict) -> dict:
        url = f"{self._base_url}{path}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                status = response.status
                content_type = response.headers.get("Content-Type", "")
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:  # 4xx/5xx
            status = exc.code
            content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
            text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            reason = classify_failure(status, content_type, text)
            _raise(reason or "parse_failed", status=status, text=text)
            raise  # pragma: no cover - _raise 必定抛出

        reason = classify_failure(status, content_type, text)
        if reason:
            _raise(reason, status=status, text=text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ParseFailed(f"返回体不是 JSON：{exc.msg}", detail=text[:200]) from exc

        err_code = data.get("errCode", 0)
        reason = business_error_reason(err_code)
        if reason == "login_required":
            raise LoginRequired(
                f"接口返回 errCode={err_code} errMsg={data.get('errMsg')!r}：登录态过期，需重新扫码",
                detail=text[:200],
                code=err_code,
            )
        if reason:
            raise ParseFailed(
                f"接口返回 errCode={err_code} errMsg={data.get('errMsg')!r}", detail=text[:200], code=err_code
            )
        return data

    # --- 业务 -----------------------------------------------------------

    def list_posts(
        self,
        *,
        page_size: int = 20,
        current_page: int = 1,
        start_time: int | None = None,
        end_time: int | None = None,
        sort: str = "createTime",
        order: str = "desc",
        timestamp_ms: str | None = None,
        finder_id: str = "",
    ) -> dict:
        payload: dict[str, object] = {
            "pageSize": page_size,
            "currentPage": current_page,
            "userpageType": 11,
            "stickyOrder": False,
            "sort": sort,
            "order": order,
            "timestamp": timestamp_ms or "",
            "_log_finder_uin": "",
            "_log_finder_id": finder_id,
            "rawKeyBuff": "",
            "pluginSessionId": None,
            "scene": 7,
            "reqScene": 7,
        }
        if start_time is not None:
            payload["startTime"] = start_time
        if end_time is not None:
            payload["endTime"] = end_time
        return self.post_json(POST_LIST_PATH, payload)
