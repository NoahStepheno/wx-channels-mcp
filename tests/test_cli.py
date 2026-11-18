"""CLI 接线：login 子命令、300333 端到端分桶、cookie 只从 auth.session 读。"""

from __future__ import annotations

import json

import pytest

from wx_channels_mcp import cli
from wx_channels_mcp.fetch.errors import LoginRequired


def test_parser_exposes_login_and_state():
    parser = cli.build_parser()
    args = parser.parse_args(["login", "--cookies", "c.json", "--profile", "p"])
    assert args.func is cli._cmd_login
    assert args.timeout == 300.0

    sync_args = parser.parse_args(["sync", "--state", "s.json"])
    assert sync_args.state == "s.json"


def test_login_command_failure_is_readable_and_secret_free(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise LoginRequired("等待扫码超时（300s），缺少 cookie：sessionid")

    monkeypatch.setattr(cli, "login", boom)
    code = cli.main(["login", "--cookies", "c.json"])

    out = capsys.readouterr().out
    assert code == 2
    assert json.loads(out)["login_status"] == "login_required"
    assert "SECRET" not in out


def test_login_command_success_prints_status_only(monkeypatch, capsys):
    payload = {"login_status": "ok", "cookies_path": "c.json", "cookie_names": ["sessionid", "wxuin"]}
    monkeypatch.setattr(cli, "login", lambda *a, **k: payload)

    code = cli.main(["login", "--cookies", "c.json"])

    out = capsys.readouterr().out
    assert code == 0
    assert json.loads(out)["login_status"] == "ok"
    assert "sessionid=" not in out


def test_expired_cookie_end_to_end_is_login_required(tmp_path, capsys):
    """真机同款现象：errCode=300333 -> login_required + next_action，不是 parse_failed。"""
    cookies = tmp_path / "cookies.json"
    cookies.write_text(json.dumps({"sessionid": "stale", "wxuin": "1"}))
    state = tmp_path / "login_state.json"

    class Api:
        def __init__(self, *a, **k):
            pass

        def list_posts(self, **kwargs):
            raise LoginRequired("接口返回 errCode=300333 errMsg='request failed'", code=300333)

    monkeypatch_target = cli.WxApi
    cli.WxApi = Api
    try:
        code = cli.main(
            ["sync", "--dry-run", "--cookies", str(cookies), "--state", str(state), "--json", str(tmp_path / "o.json")]
        )
    finally:
        cli.WxApi = monkeypatch_target

    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert out["status"] == "login_required"
    assert "wx-channels-mcp login" in out["error"]["next_action"]
    assert "stale" not in json.dumps(out)
    assert state.is_file()
    assert "stale" not in state.read_text()


def test_missing_cookie_file_raises_clearly(tmp_path):
    with pytest.raises(FileNotFoundError):
        cli.main(["sync", "--dry-run", "--cookies", str(tmp_path / "nope.json"), "--state", str(tmp_path / "s.json")])


def test_login_command_prints_path_but_never_cookie_values(tmp_path, monkeypatch, capsys):
    """回声要求：路径要打印，内容一个字都不能进日志/JSON 摘要。"""
    from wx_channels_mcp.auth.login import login as real_login

    class Session:
        def cookies(self):
            return [{"name": "sessionid", "value": "SECRET"}, {"name": "wxuin", "value": "WXUIN-778899"}]

        def close(self):
            pass

    cookies = tmp_path / "cookies.json"
    def fake_login(cookies_path, **kwargs):
        kwargs.pop("session_factory", None)
        kwargs.pop("state", None)
        return real_login(
            cookies_path,
            session_factory=lambda _p: Session(),
            sleep=lambda _s: None,
            state=cli.SessionState(tmp_path / "s.json"),
            **kwargs,
        )

    monkeypatch.setattr(cli, "login", fake_login)

    code = cli.main(["login", "--cookies", str(cookies)])
    out = capsys.readouterr().out

    assert code == 0
    assert str(cookies) in out                      # 路径要能看见
    assert "SECRET" not in out                      # 值一个字都不许出
    assert "WXUIN-778899" not in out                # wxuin 也不进摘要


def test_login_command_prints_identity(monkeypatch, capsys):
    payload = {
        "login_status": "ok",
        "cookies_path": "c.json",
        "cookie_names": ["sessionid", "wxuin"],
        "identity": {"nickname": "小满的视频号", "video_count": 1},
        "identity_unverified": False,
    }
    monkeypatch.setattr(cli, "login", lambda *a, **k: payload)

    cli.main(["login", "--cookies", "c.json"])

    out = json.loads(capsys.readouterr().out)
    assert out["identity"]["video_count"] == 1
    assert out["identity_unverified"] is False


def test_login_command_identity_unverified_is_visible(monkeypatch, capsys):
    payload = {
        "login_status": "ok",
        "cookies_path": "c.json",
        "cookie_names": ["sessionid", "wxuin"],
        "identity": None,
        "identity_unverified": True,
        "message": "未能确认登录身份，请人工确认扫的是哪个视频号",
    }
    monkeypatch.setattr(cli, "login", lambda *a, **k: payload)

    cli.main(["login", "--cookies", "c.json"])

    out = json.loads(capsys.readouterr().out)
    assert out["identity_unverified"] is True
    assert "未能确认登录身份" in out["message"]


def test_not_logged_in_exits_nonzero(monkeypatch, capsys):
    """v6.8：not_logged_in 必须 exit 非 0，别让脚本把"没登进去"当成功。"""
    payload = {
        "login_status": "not_logged_in",
        "cookies_path": "c.json",
        "probe": "error",
        "message": "扫码未完成或会话立即失效；请重新扫码",
    }
    monkeypatch.setattr(cli, "login", lambda *a, **k: payload)

    code = cli.main(["login", "--cookies", "c.json"])

    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert out["login_status"] == "not_logged_in"
