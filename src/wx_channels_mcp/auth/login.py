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
    probe_video_count_detailed,
)
from wx_channels_mcp.fetch.client import EXPIRED_SESSION_CODE
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

    def page_url(self) -> str: ...

    def page_text(self) -> str: ...

    def close(self) -> None: ...


SessionFactory = Callable[[str | Path], BrowserSession]


PAGE_TEXT_WAIT_SECONDS = 10.0
DEFAULT_REDIRECT_WAIT_SECONDS = 15.0
LOGIN_HOST = "channels.weixin.qq.com"


def select_login_page(pages) -> object | None:
    """从 context.pages 里挑本平台那个页；没有就退回第一页。"""
    pages = list(pages or [])
    for page in pages:
        try:
            url = page.url or ""
        except Exception:
            continue
        if LOGIN_HOST in url:
            return page
    return pages[0] if pages else None


def _inner_text(page) -> str:
    if page is None:
        return ""
    try:
        return page.inner_text("body", timeout=5_000) or ""
    except Exception:  # 还没渲染出来：交给上层重试/降级
        return ""


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
            # 关键：先清掉 profile 里的旧 cookie。
            # 否则"上次登录残留的 sessionid"会让轮询立刻拿到一对过期 cookie，
            # 表现为 login 成功但 sync 立刻 300333（2026-11-17 真机踩到）。
            try:
                self._context.clear_cookies()
            except Exception:  # 清不掉也要能继续，后面靠"cookie 必须是新出现的"兜底
                pass
            page = self._context.pages[0] if self._context.pages else self._context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            self._playwright.stop()
            raise

    def cookies(self) -> list[dict]:
        return self._context.cookies()

    def page_url(self) -> str:
        page = select_login_page(self._context.pages)
        try:
            return page.url or "" if page is not None else ""
        except Exception:
            return ""

    def page_text(self) -> str:
        """按 host 选登录页（不盲取 pages[0]），最多等 ~10s 让 SPA 渲染出文本。"""
        deadline = time.monotonic() + PAGE_TEXT_WAIT_SECONDS
        while True:
            page = select_login_page(self._context.pages)
            text = _inner_text(page)
            if text or time.monotonic() >= deadline:
                return text
            time.sleep(0.5)

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
    redirect_wait_seconds: float = DEFAULT_REDIRECT_WAIT_SECONDS,
) -> tuple[dict[str, str], str, str]:
    """轮询到 sessionid+wxuin 齐，等页面离开 login.html，再读页面文本与 URL。"""
    session = session_factory(profile_dir)
    deadline = clock() + max(timeout_seconds, 0)
    try:
        while True:
            cookies = _to_cookie_dict(session.cookies())
            if len(cookies) == len(REQUIRED_COOKIES):
                _wait_leave_login_page(session, clock=clock, sleep=sleep, limit=redirect_wait_seconds)
                return cookies, _safe_page_text(session), _safe_page_url(session)
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


def _wait_leave_login_page(
    session: BrowserSession, *, clock: Callable[[], float], sleep: Callable[[float], None], limit: float
) -> None:
    """扫码成功后页面应离开 login.html；给它一点时间跳转，超时由上层判 not_logged_in。"""
    url = _safe_page_url(session)
    if not url:  # 拿不到页面 URL（替身/无浏览器）：不阻塞，交给探测结果判定
        return
    deadline = clock() + max(limit, 0)
    while clock() < deadline:
        if "login.html" not in _safe_page_url(session):
            return
        sleep(0.5)


def _safe_page_url(session: BrowserSession) -> str:
    reader = getattr(session, "page_url", None)
    if reader is None:
        return ""
    try:
        return str(reader() or "")
    except Exception:
        return ""


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
    cookies, _text, _url = capture_cookies_and_identity(
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


def _default_probe(cookies: dict[str, str]) -> tuple[str, int | None, int | None]:
    from wx_channels_mcp.fetch.client import WxApi

    return probe_video_count_detailed(WxApi(cookies))


def resolve_identity(
    page_text: str,
    cookies: dict[str, str],
    *,
    probe: Callable[[dict[str, str]], tuple[str, int | None, int | None]],
) -> tuple[dict | None, str, int | None]:
    """把"拿到的字段"拼成 identity，一个都没有才是 None（spec v6.2 机械定义）。

    - 昵称/视频号ID 来自页面文本，解析不出就不带该键；
    - `video_count` 只来自一次 `post_list` 的 `totalCount`，拿不到就不带该键；
    - `probe` 是登录动作的结论，独立于昵称解析结果，永远由调用方放在顶层。
    """
    identity: dict[str, object] = dict(parse_identity(page_text) or {})
    status, count, code = probe(cookies)
    if count is not None and status == PROBE_OK:
        identity["video_count"] = count
    return (identity or None), status, code


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
    identity_probe: Callable[[dict[str, str]], tuple[str, int | None, int | None]] | None = None,
    redirect_wait_seconds: float = DEFAULT_REDIRECT_WAIT_SECONDS,
) -> dict:
    """扫码 -> 落 cookies（0600）-> 记登录时间线 -> 报出登录身份。绝不含 cookie 值。"""
    cookies, page_text, page_url = capture_cookies_and_identity(
        session_factory or open_browser_session,
        profile_dir=profile_dir,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        sleep=sleep,
        clock=clock,
        redirect_wait_seconds=redirect_wait_seconds,
    )
    identity, probe, code = resolve_identity(page_text, cookies, probe=identity_probe or _default_probe)

    # spec v6.8/v6.9：先判定，**只有真登进去才落盘与记时间线**。
    # 失败也写会（1）把还能用的 cookie 换成登录页的匿名 cookie；
    # （2）记下假的 login_at，让后续 300333 落进 600s 窗口被判 parse_failed —— 正是要消灭的误诊。
    still_on_login_page = bool(page_url) and "login.html" in page_url
    session_expired = code == EXPIRED_SESSION_CODE
    logged_in = not (still_on_login_page or session_expired)

    result = {
        "login_status": "ok" if logged_in else "not_logged_in",
        "cookies_path": str(cookies_path),
        "cookies_written": False,
        "cookie_names": sorted(cookies),
        "login_at": None,
        "profile_dir": str(profile_dir),
        "identity": identity,
        "identity_unverified": identity is None,
        "page_url": page_url,
        # 探测结果独立于 identity：即使身份拿不到，也要能看出是"号不对"还是"查不到"
        "probe": probe,
    }

    if logged_in:
        target = write_cookies(cookies_path, cookies)
        state = state or SessionState(DEFAULT_LOGIN_STATE_PATH)
        state.note_login(now=_utcnow())
        result["cookies_path"] = str(target)
        result["cookies_written"] = True
        result["login_at"] = state.login_at()

    if result["login_status"] != "ok":
        reason = "页面仍停在登录页" if still_on_login_page else "扫码后会话立即失效（errCode=300333）"
        result["message"] = (
            f"扫码未完成或会话立即失效：{reason}；请重新扫码，扫完确认页面已进入创作者中心"
            "（本次未写入 cookies，也未记录登录时间）"
        )
    elif probe == PROBE_DENIED:
        result["message"] = (
            "该账号对 post_list 无权限（errCode=300002），很可能扫错了微信号："
            "请重新扫码，并确认是平时发布视频的那个号"
        )
    elif probe == PROBE_ERROR:
        result["message"] = "身份探测失败（无法读取作品数），已拿到的字段照常输出；请人工确认扫的是哪个视频号"
    elif identity is None:
        result["message"] = "未能确认登录身份（只有作品数、没有昵称也算不出是哪个号），请人工确认扫的是哪个视频号"
    return result
