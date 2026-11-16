"""登录态：cookie 落盘/读取 + 过期判定（spec f2dbf0a）。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOGIN_STATE_PATH = "data/login_state.json"
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
    规则：`login_at` 为空或 `login_at <= last_300333_at` -> login_required；
          `login_at > last_300333_at` -> parse_failed（新登录过仍失败，不是登录问题）。
    mtime 会因拷贝/解压/`cp -p` 漂移，所以显式字段才是事实。
    """

    def __init__(self, path: str | Path = DEFAULT_LOGIN_STATE_PATH) -> None:
        self._path = Path(path)

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
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

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
        """收到 300333 时的判定：返回 (reason, next_action)。"""
        moment = (now or _utcnow()).astimezone(timezone.utc)
        data = self._load()
        login_at = _parse(data.get("login_at"))
        last_333 = _parse(data.get("last_300333_at"))

        if login_at is not None and last_333 is not None and login_at > last_333:
            # 新登录之后仍然 300333：不是登录问题，降级为解析问题（不再刷新时间戳，保持稳定）
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
