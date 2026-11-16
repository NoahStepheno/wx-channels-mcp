"""`wx-channels-mcp login`：开本平台独立 profile，等人工扫码，成功后落 cookies（0600）。

登录是 Human 的动作；落盘、鉴权字段过滤、过期提示都是工具的事（spec f2dbf0a / P0.5）。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from wx_channels_mcp.auth.identity import (
    PROBE_DENIED,
    PROBE_ERROR,
    PROBE_OK,
    parse_identity,
    probe_video_count,
)
from wx_channels_mcp.auth.session import (
    DEFAULT_COOKIES_PATH,
    DEFAULT_LOGIN_STATE_PATH,
    REQUIRED_COOKIES,
    SessionState,
    read_cookies,
    write_cookies,
)
from wx_channels_mcp.fetch.errors import LoginRequired

LOGIN_URL = "https://channels.weixin.qq.com/platform"
DEFAULT_PROFILE_DIR = ".botharness/login-profile"
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_POLL_SECONDS = 2.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BrowserSession(Protocol):
    """登录页会话：只暴露取 cookie 与关闭，便于单测替身。"""

    def cookies(self) -> list[dict]: ...

    def page_text(self) -> str: ...

    def close(self) -> None: ...


SessionFactory = Callable[[str | Path], BrowserSession]


class PlaywrightSession:
    """真实浏览器会话：复用本平台自己的 profile，不与其他项目共用。"""

    def __init__(self, profile_dir: str | Path, *, channel: str = "chrome", headless: bool = False) -> None:
        from playwright.sync_api import sync_playwright  # 懒加载：无浏览器的环境照样能跑 export

        self._profile_dir = str(profile_dir)
        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                self._profile_dir,
                channel=channel,
                headless=headless,
            )
            page = self._context.pages[0] if self._context.pages else self._context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            self._playwright.stop()
            raise

    def cookies(self) -> list[dict]:
        return self._context.cookies()

    def page_text(self) -> str:
        page = self._context.pages[0] if self._context.pages else None
        if page is None:
            return ""
        try:
            return page.inner_text("body", timeout=5_000) or ""
        except Exception:  # 页面还没渲染出来：身份降级，不影响登录
            return ""

    def close(self) -> None:
        self._context.close()
        self._playwright.stop()


def _to_cookie_dict(raw: list[dict]) -> dict[str, str]:
    """只保留鉴权必需的两个字段；其它 cookie 一律不落盘。"""
    wanted = {str(item.get("name")): str(item.get("value", "")) for item in raw or []}
    return {name: wanted[name] for name in REQUIRED_COOKIES if wanted.get(name)}


def capture_cookies_and_identity(
    session_factory: SessionFactory,
    *,
    profile_dir: str | Path,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, str], str]:
    """打开登录页轮询到 sessionid+wxuin 齐，再顺手读一次页面文本（身份用）。"""
    session = session_factory(profile_dir)
    deadline = clock() + max(timeout_seconds, 0)
    try:
        while True:
            cookies = _to_cookie_dict(session.cookies())
            if len(cookies) == len(REQUIRED_COOKIES):
                return cookies, _safe_page_text(session)
            if clock() >= deadline:
                missing = [name for name in REQUIRED_COOKIES if name not in cookies]
                raise LoginRequired(
                    f"等待扫码超时（{timeout_seconds:g}s），缺少 cookie：{', '.join(missing)}；"
                    "请在打开的浏览器里完成扫码后重试"
                )
            sleep(poll_seconds)
    finally:
        try:
            session.close()
        except Exception:  # 关不掉不能掩盖扫码结果
            pass


def _safe_page_text(session: BrowserSession) -> str:
    reader = getattr(session, "page_text", None)
    if reader is None:
        return ""
    try:
        return str(reader() or "")
    except Exception:
        return ""


def capture_cookies(
    session_factory: SessionFactory,
    *,
    profile_dir: str | Path,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, str]:
    """只取 cookie 的兼容入口（身份解析走 `capture_cookies_and_identity`）。"""
    cookies, _text = capture_cookies_and_identity(
        session_factory,
        profile_dir=profile_dir,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        sleep=sleep,
        clock=clock,
    )
    return cookies


def open_browser_session(profile_dir: str | Path) -> BrowserSession:
    return PlaywrightSession(profile_dir)


def _default_probe(cookies: dict[str, str]) -> tuple[str, int | None]:
    from wx_channels_mcp.fetch.client import WxApi

    return probe_video_count(WxApi(cookies))


def resolve_identity(
    page_text: str, cookies: dict[str, str], *, probe: Callable[[dict[str, str]], tuple[str, int | None]]
) -> tuple[dict | None, str]:
    """昵称来自页面文本、视频数**只**来自 `post_list.totalCount`。

    返回 `(identity, probe)`；`identity` 为 None 时调用方必须降级并说清原因。
    """
    base = parse_identity(page_text)
    status, count = probe(cookies)
    if base is None or status != PROBE_OK or count is None:
        return None, status if base is not None else PROBE_ERROR
    return {**base, "video_count": count, "probe": status}, status


def login(
    cookies_path: str | Path = DEFAULT_COOKIES_PATH,
    *,
    profile_dir: str | Path = DEFAULT_PROFILE_DIR,
    session_factory: SessionFactory | None = None,
    state: SessionState | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    identity_probe: Callable[[dict[str, str]], tuple[str, int | None]] | None = None,
) -> dict:
    """扫码 -> 落 cookies（0600）-> 记登录时间线 -> 报出登录身份。绝不含 cookie 值。"""
    cookies, page_text = capture_cookies_and_identity(
        session_factory or open_browser_session,
        profile_dir=profile_dir,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        sleep=sleep,
        clock=clock,
    )
    target = write_cookies(cookies_path, cookies)
    state = state or SessionState(DEFAULT_LOGIN_STATE_PATH)
    state.note_login(now=_utcnow())

    identity, probe = resolve_identity(page_text, cookies, probe=identity_probe or _default_probe)
    result = {
        "login_status": "ok",
        "cookies_path": str(target),
        "cookie_names": sorted(cookies),
        "login_at": state.login_at(),
        "profile_dir": str(profile_dir),
        "identity": identity,
        "identity_unverified": identity is None,
        # 探测结果独立于 identity：即使身份拿不到，也要能看出是"号不对"还是"查不到"
        "probe": probe,
    }
    if identity is None:
        if probe == PROBE_DENIED:
            result["message"] = (
                "该账号对 post_list 无权限（errCode=300002），很可能扫错了微信号："
                "请重新扫码，并确认是平时发布视频的那个号"
            )
        elif probe == PROBE_ERROR:
            result["message"] = "未能确认登录身份：身份探测失败（无法读取作品数或页面文本），请人工确认扫的是哪个视频号"
        else:  # pragma: no cover - 理论上不可达，防御性保留
            result["message"] = "未能确认登录身份，请人工确认扫的是哪个视频号（视频号名称/视频数）"
    return result
