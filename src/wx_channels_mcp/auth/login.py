"""`wx-channels-mcp login`：开本平台独立 profile，等人工扫码，成功后落 cookies（0600）。

登录是 Human 的动作；落盘、鉴权字段过滤、过期提示都是工具的事（spec f2dbf0a / P0.5）。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

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

    def close(self) -> None:
        self._context.close()
        self._playwright.stop()


def _to_cookie_dict(raw: list[dict]) -> dict[str, str]:
    """只保留鉴权必需的两个字段；其它 cookie 一律不落盘。"""
    wanted = {str(item.get("name")): str(item.get("value", "")) for item in raw or []}
    return {name: wanted[name] for name in REQUIRED_COOKIES if wanted.get(name)}


def capture_cookies(
    session_factory: SessionFactory,
    *,
    profile_dir: str | Path,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, str]:
    """打开登录页并轮询，直到 sessionid + wxuin 都出现；超时抛 LoginRequired。"""
    session = session_factory(profile_dir)
    deadline = clock() + max(timeout_seconds, 0)
    try:
        while True:
            cookies = _to_cookie_dict(session.cookies())
            if len(cookies) == len(REQUIRED_COOKIES):
                return cookies
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


def open_browser_session(profile_dir: str | Path) -> BrowserSession:
    return PlaywrightSession(profile_dir)


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
) -> dict:
    """扫码 -> 落 cookies（0600）-> 记登录时间线。返回打印用摘要，绝不含 cookie 值。"""
    cookies = capture_cookies(
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
    return {
        "login_status": "ok",
        "cookies_path": str(target),
        "cookie_names": sorted(cookies),
        "login_at": state.login_at(),
        "profile_dir": str(profile_dir),
    }
