"""登录态：300333 该归 login_required 还是 parse_failed，以及 cookies 落盘/读取（spec f2dbf0a）。

判定规则（写死）：
- 旧 cookie 第一次遇 300333 -> login_required + next_action；
- 重新扫码后**仍** 300333 -> 才降 parse_failed（原样带 errCode），且保持稳定；
- 状态文件只记时间戳，绝不存 cookie 值。
"""

from __future__ import annotations

import json
import stat
from datetime import datetime, timedelta, timezone

import pytest

from wx_channels_mcp.auth.session import (
    NEXT_ACTION_RELOGIN,
    SessionState,
    read_cookies,
    write_cookies,
)

NOW = datetime(2026, 11, 16, 1, 40, tzinfo=timezone.utc)


def _state(tmp_path) -> SessionState:
    return SessionState(tmp_path / "login_state.json")


def test_first_expiry_is_login_required(tmp_path):
    state = _state(tmp_path)
    reason, next_action = state.classify_expiry(now=NOW)

    assert reason == "login_required"
    assert "wx-channels-mcp login" in next_action
    assert next_action == NEXT_ACTION_RELOGIN


def test_repeat_expiry_without_rescan_stays_login_required(tmp_path):
    state = _state(tmp_path)
    state.classify_expiry(now=NOW)
    reason, _ = state.classify_expiry(now=NOW + timedelta(hours=3))

    assert reason == "login_required"


def test_expiry_after_rescan_still_failing_downgrades_to_parse_failed(tmp_path):
    state = _state(tmp_path)
    state.classify_expiry(now=NOW)                     # 报过期，等人扫码
    state.note_login(now=NOW + timedelta(minutes=20))  # 人扫完了
    reason, next_action = state.classify_expiry(now=NOW + timedelta(minutes=21))

    assert reason == "parse_failed"
    assert next_action is None


def test_parse_failed_stays_stable_until_next_login(tmp_path):
    state = _state(tmp_path)
    state.classify_expiry(now=NOW)
    state.note_login(now=NOW + timedelta(minutes=20))
    state.classify_expiry(now=NOW + timedelta(minutes=21))

    reason, _ = state.classify_expiry(now=NOW + timedelta(minutes=40))
    assert reason == "parse_failed"


def test_state_file_holds_no_cookie_values(tmp_path):
    cookies_path = tmp_path / "cookies.json"
    write_cookies(cookies_path, {"sessionid": "SECRET-SESSION-VALUE", "wxuin": "1234567890"})

    state = _state(tmp_path)
    state.classify_expiry(now=NOW)
    state.note_login(now=NOW)
    raw = (tmp_path / "login_state.json").read_text(encoding="utf-8")

    assert "SECRET-SESSION-VALUE" not in raw
    assert "1234567890" not in raw
    assert set(json.loads(raw)) <= {"login_at", "last_300333_at"}


def test_write_cookies_is_0600_and_roundtrips(tmp_path):
    path = tmp_path / "cookies.json"
    write_cookies(path, {"sessionid": "s", "wxuin": "u"})

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_cookies(path) == {"sessionid": "s", "wxuin": "u"}


def test_read_cookies_accepts_playwright_list(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps([{"name": "sessionid", "value": "s"}, {"name": "wxuin", "value": "u"}]))

    assert read_cookies(path) == {"sessionid": "s", "wxuin": "u"}


def test_session_state_missing_file_is_empty(tmp_path):
    state = _state(tmp_path)

    assert state.login_at() is None
    assert state.last_300333_at() is None


def test_session_state_corrupt_file_does_not_crash(tmp_path):
    path = tmp_path / "login_state.json"
    path.write_text("{not json")

    state = SessionState(path)
    reason, _ = state.classify_expiry(now=NOW)
    assert reason == "login_required"


@pytest.mark.parametrize("now", [NOW, None])
def test_classify_expiry_accepts_optional_now(tmp_path, now):
    reason, _ = _state(tmp_path).classify_expiry(now=now)
    assert reason in {"login_required", "parse_failed"}


# --- 回声钉死的状态机：只看两个显式时间戳的三种关系（无需真扫码） ----------------


def _write_state(tmp_path, data):
    (tmp_path / "login_state.json").write_text(json.dumps(data), encoding="utf-8")
    return SessionState(tmp_path / "login_state.json")


def test_state_machine_login_at_empty_is_login_required(tmp_path):
    state = _write_state(tmp_path, {"last_300333_at": "2026-11-16T01:00:00+00:00"})

    assert state.classify_expiry(now=NOW)[0] == "login_required"


def test_state_machine_login_at_older_or_equal_is_login_required(tmp_path):
    state = _write_state(
        tmp_path,
        {"login_at": "2026-11-16T01:00:00+00:00", "last_300333_at": "2026-11-16T01:00:00+00:00"},
    )

    assert state.classify_expiry(now=NOW)[0] == "login_required"


def test_state_machine_login_at_newer_is_parse_failed_without_touching_state(tmp_path):
    state = _write_state(
        tmp_path,
        {"login_at": "2026-11-16T01:20:00+00:00", "last_300333_at": "2026-11-16T01:00:00+00:00"},
    )

    assert state.classify_expiry(now=NOW) == ("parse_failed", None)
    assert state.last_300333_at() == "2026-11-16T01:00:00+00:00", "降级后不刷新时间戳，保持稳定"


def test_state_machine_consecutive_300333_stays_login_required(tmp_path):
    state = _state(tmp_path)

    first = state.classify_expiry(now=NOW)
    second = state.classify_expiry(now=NOW + timedelta(minutes=1))

    assert first[0] == second[0] == "login_required"
    assert state.last_300333_at() == (NOW + timedelta(minutes=1)).isoformat()


def test_state_machine_ignores_cookie_file_mtime(tmp_path):
    """cp -p / 解压 会改 mtime；判定不能跟着飘。"""
    cookies = tmp_path / "cookies.json"
    write_cookies(cookies, {"sessionid": "s", "wxuin": "u"})
    state = _state(tmp_path)
    state.classify_expiry(now=NOW)                       # login_required
    state.note_login(now=NOW + timedelta(minutes=5))     # 人扫了码

    cookies.touch()  # 模拟文件被重新拷贝，mtime 变了

    assert state.classify_expiry(now=NOW + timedelta(minutes=6))[0] == "parse_failed"


def test_write_cookies_creates_file_0600_at_open_time(tmp_path, monkeypatch):
    """不允许存在"先默认 umask 建、再 chmod"的窗口期：os.open 那一刻就得是 0600。"""
    import os as os_mod
    from pathlib import Path as Path_mod

    calls: list[tuple[str, int]] = []
    real_open = os_mod.open

    def spy(path, flags, mode=0o777, *args, **kwargs):
        calls.append((Path_mod(path).name, mode))
        return real_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(os_mod, "open", spy)
    old_umask = os_mod.umask(0o022)  # 正常 umask：若走默认 open()，文件会是 0644
    try:
        path = write_cookies(tmp_path / "cookies.json", {"sessionid": "s", "wxuin": "u"})
    finally:
        os_mod.umask(old_umask)

    assert calls, "应当用 os.open 显式给权限"
    assert all(mode == 0o600 for _name, mode in calls)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_cookies_overwrites_tightening_existing_permissions(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)

    write_cookies(path, {"sessionid": "s", "wxuin": "u"})

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_cookies_failure_leaves_no_tmp(tmp_path, monkeypatch):
    import wx_channels_mcp.auth.session as session_mod

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(session_mod.json, "dumps", boom)
    with pytest.raises(RuntimeError):
        write_cookies(tmp_path / "cookies.json", {"sessionid": "s"})

    assert list(tmp_path.iterdir()) == []


def test_tmp_file_is_0600_while_being_written(tmp_path, monkeypatch):
    """只测最终态测不出窗口期：这里在**写入那一刻**断言 tmp 已经是 0600。"""
    import wx_channels_mcp.auth.session as session_mod

    real_fdopen = session_mod.os.fdopen
    tmp_path_observed: dict[str, int] = {}

    class _SpyHandle:
        def __init__(self, handle) -> None:
            self._handle = handle

        def write(self, data):
            tmp_path_observed["mode"] = stat.S_IMODE((tmp_path / "cookies.json.tmp").stat().st_mode)
            return self._handle.write(data)

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self._handle.close()
            return False

    def spy_fdopen(fd, *args, **kwargs):
        return _SpyHandle(real_fdopen(fd, *args, **kwargs))

    monkeypatch.setattr(session_mod.os, "fdopen", spy_fdopen)
    write_cookies(tmp_path / "cookies.json", {"sessionid": "SECRET", "wxuin": "u"})

    assert tmp_path_observed["mode"] == 0o600, "写入过程中临时文件就已经必须是 0600"
