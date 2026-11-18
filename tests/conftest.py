"""测试隔离：绝不碰仓库里的真实 `data/`（登录时间线写进去会污染真机判定）。

背景（2026-11-16 自查发现）：`tests/test_login_command.py` 直接跑真实 `login()`，
而它默认把 `login_at` 写进 `data/login_state.json`。真机上就会把本该 `login_required`
的 300333 判成 `parse_failed` —— 正好是要消除的那种误诊。所以这里统一把默认路径指到 tmp。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_login_state(tmp_path, monkeypatch):
    from wx_channels_mcp import cli
    from wx_channels_mcp.auth import login as login_mod
    from wx_channels_mcp.auth import session as session_mod

    isolated = tmp_path / "login_state.json"
    monkeypatch.setattr(session_mod, "DEFAULT_LOGIN_STATE_PATH", isolated)
    monkeypatch.setattr(login_mod, "DEFAULT_LOGIN_STATE_PATH", isolated)
    monkeypatch.setattr(cli, "DEFAULT_LOGIN_STATE_PATH", isolated)
    return isolated
