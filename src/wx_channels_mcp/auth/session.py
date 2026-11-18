"""登录态：cookie 落盘/读取 + 过期判定（spec f2dbf0a）。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_LOGIN_STATE_PATH = "data/login_state.json"
# "重新扫码后紧接着仍失败"的窗口（spec v6）。越短越安全：
# 误判 login_required 只是多扫一次且必然可恢复；误判 parse_failed 会把人引去查 parser。
RECENT_LOGIN_WINDOW = timedelta(minutes=10)
DEFAULT_COOKIES_PATH = ".botharness/cookies.json"
COOKIE_FILE_MODE = 0o600
REQUIRED_COOKIES = ("sessionid", "wxuin")

# 300333 = 登录态过期：要做的是重新扫码，不是改 parser
NEXT_ACTION_RELOGIN = "重新扫码登录后重试一次：wx-channels-mcp login"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def read_cookies(path: str | Path) -> dict[str, str]:
    """读 cookie 文件：支持 `{name: value}` 字典和 Playwright 的 `[{name, value}]` 列表。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    return {str(item["name"]): str(item["value"]) for item in raw}


def write_cookies(path: str | Path, cookies: dict[str, str]) -> Path:
    """原子写入并强制 0600 —— sessionid 等价于账号密码。

    临时文件在 `os.open` 那一刻就是 0600：不存在"默认 umask 可读"的窗口期。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, COOKIE_FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(cookies, ensure_ascii=False))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(target)
    target.chmod(COOKIE_FILE_MODE)  # 覆盖旧文件时也把权限收紧
    return target


class SessionState:
    """登录态时间线：只存时间戳，不存任何 cookie 值。

    判定只看两个显式时间戳（回声钉死，不用文件 mtime、不用重试次数）：
    - `login_at`：login 子命令成功时写入；
    - `last_300333_at`：每次遇到 300333 时写入。

    spec v6 规则（窗口化，`RECENT_LOGIN_WINDOW=10min`）：
    - `300333` 且 `login_at > last_300333_at` 且 `now - login_at < 10min` -> parse_failed
      （刚扫完还失败，那就不是登录问题）
    - 其余全部 -> login_required，并刷新 `last_300333_at`（绝不改 `login_at`）
    - `now < login_at`（时钟回退/文件被改）-> parse_failed，并把原始差值放进 `note`，不静默
    mtime 会因拷贝/解压/`cp -p` 漂移，所以显式字段才是事实。
    """

    def __init__(self, path: str | Path = DEFAULT_LOGIN_STATE_PATH) -> None:
        self._path = Path(path)
        self.last_note: str | None = None
        self._save_failed = False
        self._save_error: str | None = None

    # --- 状态读写 -------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict) -> None:
        """写时间线；失败不抛（spec v6.7）——记账失败不许改变判定或让 sync 变 traceback。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            self._save_failed = True
            self._save_error = f"{type(exc).__name__}: {exc}"
            new_note = (
                f"未能刷新 last_300333_at（只读/权限/磁盘问题）：{self._save_error}；"
                "判定照常给出，但诊断时间线未更新"
            )
            if self.last_note is None:
                self.last_note = new_note

    def login_at(self) -> str | None:
        return self._load().get("login_at")

    def last_300333_at(self) -> str | None:
        return self._load().get("last_300333_at")

    # --- 事件 -----------------------------------------------------------

    def note_login(self, *, now: datetime | None = None) -> None:
        """`login` 成功后调用：记一条登录时间线证据。"""
        moment = now or _utcnow()
        data = self._load()
        data["login_at"] = moment.astimezone(timezone.utc).isoformat()
        self._save(data)

    def classify_expiry(self, *, now: datetime | None = None) -> tuple[str, str | None]:
        """收到 300333 时的判定：返回 (reason, next_action)；`last_note` 带排查线索。"""
        moment = (now or _utcnow()).astimezone(timezone.utc)
        data = self._load()
        login_at = _parse(data.get("login_at"))
        last_333 = _parse(data.get("last_300333_at"))
        self.last_note = None

        if login_at is not None and login_at > moment:
            # 时钟回退或状态文件被改：不静默当成"窗口内"，把原始差值摆出来
            delta = (moment - login_at).total_seconds()
            self.last_note = (
                f"系统时间早于 login_at（now={moment.isoformat()}, login_at={login_at.isoformat()}, "
                f"差值={delta:+.0f}s）：按 parse_failed 处理，请检查本机时钟或状态文件"
            )
            return "parse_failed", None

        if (
            login_at is not None
            and last_333 is not None
            and login_at > last_333
            and (moment - login_at) < RECENT_LOGIN_WINDOW
        ):
            # 刚扫完（<10min）仍 300333：不是登录问题，降级；不刷新时间戳，保持稳定
            return "parse_failed", None

        data["last_300333_at"] = moment.isoformat()
        self._save(data)
        return "login_required", NEXT_ACTION_RELOGIN


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
