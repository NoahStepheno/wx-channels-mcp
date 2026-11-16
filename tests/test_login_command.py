"""`wx-channels-mcp login`：开独立 profile 等扫码，成功后落 cookies（0600），失败要可读。"""

from __future__ import annotations

import stat

import pytest

from wx_channels_mcp.auth import login as login_mod
from wx_channels_mcp.auth.login import login
from wx_channels_mcp.fetch.errors import LoginRequired

COOKIES = [
    {"name": "sessionid", "value": "SECRET"},
    {"name": "wxuin", "value": "42"},
    {"name": "other", "value": "x"},
]


class FakeSession:
    """第 N 次轮询才拿到 cookie，用来模拟"人还在扫码"。"""

    def __init__(self, snapshots, *, fail_close=False):
        self._snapshots = list(snapshots)
        self.closed = False
        self.polls = 0
        self._fail_close = fail_close

    def cookies(self):
        self.polls += 1
        if not self._snapshots:
            return []
        return self._snapshots.pop(0) if len(self._snapshots) > 1 else self._snapshots[0]

    def close(self):
        self.closed = True
        if self._fail_close:
            raise RuntimeError("browser already gone")


def _factory(session):
    opened = {"count": 0}

    def factory(profile_dir):  # noqa: ANN001
        opened["count"] += 1
        opened["profile_dir"] = profile_dir
        return session

    factory.opened = opened
    return factory


def test_login_polls_until_sessionid_appears(tmp_path):
    session = FakeSession([[], [], COOKIES])
    path = tmp_path / "cookies.json"

    result = login(path, profile_dir=tmp_path / "profile", session_factory=_factory(session), sleep=lambda _s: None)

    assert result["login_status"] == "ok"
    assert result["cookie_names"] == ["sessionid", "wxuin"]
    assert session.polls == 3
    assert session.closed is True
    assert (path.stat().st_mode & stat.S_IMODE(0o777)) == 0o600


def test_login_keeps_only_sessionid_and_wxuin(tmp_path):
    session = FakeSession([COOKIES])
    path = tmp_path / "cookies.json"

    login(path, profile_dir=tmp_path / "profile", session_factory=_factory(session), sleep=lambda _s: None)

    assert sorted(login_mod.read_cookies(path)) == ["sessionid", "wxuin"]


def test_login_times_out_as_login_required(tmp_path):
    session = FakeSession([[]])
    path = tmp_path / "cookies.json"

    with pytest.raises(LoginRequired) as exc:
        login(
            path,
            profile_dir=tmp_path / "profile",
            session_factory=_factory(session),
            sleep=lambda _s: None,
            timeout_seconds=0,
            poll_seconds=0,
        )

    assert "扫码" in str(exc.value)
    assert session.closed is True
    assert not path.exists()


def test_login_missing_wxuin_times_out(tmp_path):
    session = FakeSession([[{"name": "sessionid", "value": "SECRET"}]])
    with pytest.raises(LoginRequired):
        login(
            tmp_path / "cookies.json",
            profile_dir=tmp_path / "profile",
            session_factory=_factory(session),
            sleep=lambda _s: None,
            timeout_seconds=0,
            poll_seconds=0,
        )


def test_login_records_login_time_for_expiry_logic(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr(login_mod, "_utcnow", lambda: datetime(2026, 11, 17, 1, 30, tzinfo=timezone.utc))
    session = FakeSession([COOKIES])
    path = tmp_path / "cookies.json"
    state_path = tmp_path / "login_state.json"
    monkeypatch.setattr(login_mod, "DEFAULT_LOGIN_STATE_PATH", state_path)

    login(path, profile_dir=tmp_path / "profile", session_factory=_factory(session), sleep=lambda _s: None)

    from wx_channels_mcp.auth.session import SessionState

    assert SessionState(state_path).login_at() == "2026-11-17T01:30:00+00:00"


def test_login_result_never_prints_secret(tmp_path):
    session = FakeSession([COOKIES])

    result = login(
        tmp_path / "cookies.json",
        profile_dir=tmp_path / "profile",
        session_factory=_factory(session),
        sleep=lambda _s: None,
    )

    assert "SECRET" not in repr(result)


def test_login_close_failure_does_not_mask_success(tmp_path):
    session = FakeSession([COOKIES], fail_close=True)

    result = login(
        tmp_path / "cookies.json",
        profile_dir=tmp_path / "profile",
        session_factory=_factory(session),
        sleep=lambda _s: None,
    )

    assert result["login_status"] == "ok"


def test_default_state_path_is_never_the_repo_data_dir(tmp_path):
    """回归：测试跑真实 login() 时，绝不许写仓库的 data/。

    真机上 `data/login_state.json` 里若多出一条假 login_at，会让 300333 被误判成
    parse_failed —— 正是 spec f2dbf0a 要消除的误诊。允许该文件真实存在（迁移后会有），
    但断言内容一字不变。
    """
    from pathlib import Path

    import wx_channels_mcp.auth.session as session_mod

    repo_data = Path(session_mod.DEFAULT_LOGIN_STATE_PATH).parent
    assert not str(repo_data.resolve()).startswith(str(Path(__file__).parents[1]))

    real_state = Path(__file__).parents[1] / "data" / "login_state.json"
    before = real_state.read_text(encoding="utf-8") if real_state.exists() else None

    session = FakeSession([COOKIES])
    login(tmp_path / "cookies.json", profile_dir=tmp_path / "p", session_factory=_factory(session), sleep=lambda _s: None)

    after = real_state.read_text(encoding="utf-8") if real_state.exists() else None
    assert after == before, "测试不许改动仓库的 data/login_state.json"


def test_select_login_page_prefers_channels_host():
    """v6.2：按 host 选页，不盲取 pages[0]（登录 profile 里可能有 about:blank）。"""
    from wx_channels_mcp.auth.login import select_login_page

    class Page:
        def __init__(self, url):
            self.url = url

    blank = Page("about:blank")
    channels = Page("https://channels.weixin.qq.com/platform")
    assert select_login_page([blank, channels]) is channels
    assert select_login_page([blank]) is blank
    assert select_login_page([]) is None
