"""v5：`login` 必须回答"登进去的是哪个号"（spec 6.1.1，11-16 登错号事故）。

反例就是这次：`login_status: ok` + cookie 0600 + 两个 cookie 齐全，但号不对，
`post_list` 返 300002，人能看到的只有"成功"。
"""

from __future__ import annotations

import json
import stat

import pytest

from wx_channels_mcp.auth import login as login_mod
from wx_channels_mcp.auth.identity import fetch_video_count, parse_identity
from wx_channels_mcp.auth.login import login
from wx_channels_mcp.fetch.errors import WxError

# 11-16 事故现场真实页面文本（headless 读出来的）
WRONG_ACCOUNT_TEXT = (
    "视频号 · 助手 | 1 | 用户0284223142 | 申请认证 | 视频号ID: | 视频28 | 关注者0 | "
    "如何让更多的人看到你的视频？ | 2021年6月22日"
)
RIGHT_ACCOUNT_TEXT = "视频号 · 助手 | 1 | 小满的视频号 | 视频1 | 关注者0"


def test_parse_identity_reads_nickname_and_video_count():
    identity = parse_identity(WRONG_ACCOUNT_TEXT)

    assert identity == {"nickname": "用户0284223142", "video_count": 28}


def test_parse_identity_right_account():
    assert parse_identity(RIGHT_ACCOUNT_TEXT)["nickname"] == "小满的视频号"
    assert parse_identity(RIGHT_ACCOUNT_TEXT)["video_count"] == 1


def test_parse_identity_includes_finder_id_when_present():
    text = "视频号 · 助手 | 1 | 用户0284223142 | 视频号ID: v2_060000227c00@finder | 视频28"

    identity = parse_identity(text)

    assert identity["finder_id"] == "v2_060000227c00@finder"


def test_parse_identity_missing_fields_returns_none():
    assert parse_identity("") is None
    assert parse_identity("登录 微信扫码") is None
    assert parse_identity("视频号 · 助手 | 用户0284223142") is None, "缺 video_count 不算身份已确认"


def test_fetch_video_count_uses_total_count():
    class Api:
        def __init__(self, payload):
            self.payload = payload

        def list_posts(self, **_kwargs):
            return {"errCode": 0, "data": self.payload}

    assert fetch_video_count(Api({"totalCount": 1})) == 1
    assert fetch_video_count(Api({})) is None


def test_fetch_video_count_swallows_api_error():
    class Api:
        def list_posts(self, **_kwargs):
            raise WxError("300002")

    assert fetch_video_count(Api()) is None


class FakeSession:
    def __init__(self, cookies, text):
        self._cookies = cookies
        self._text = text
        self.closed = False

    def cookies(self):
        return self._cookies

    def page_text(self):
        return self._text

    def close(self):
        self.closed = True


COOKIES = [{"name": "sessionid", "value": "SECRET"}, {"name": "wxuin", "value": "1"}]


def _run(tmp_path, text, *, video_count=1, fetcher=None):
    session = FakeSession(COOKIES, text)
    return login(
        tmp_path / "cookies.json",
        profile_dir=tmp_path / "p",
        session_factory=lambda _p: session,
        sleep=lambda _s: None,
        state=login_mod.SessionState(tmp_path / "state.json"),
        video_count_fetcher=fetcher or (lambda _cookies: video_count),
    )


def test_login_reports_identity_when_parsed(tmp_path):
    result = _run(tmp_path, RIGHT_ACCOUNT_TEXT, video_count=1)

    assert result["login_status"] == "ok"
    assert result["identity"] == {"nickname": "小满的视频号", "video_count": 1}
    assert result["identity_unverified"] is False
    assert "identity" in json.dumps(result)


def test_login_prefers_measured_video_count_over_page_text(tmp_path):
    """页面文本可能滞后；接口 totalCount 更可信。"""
    result = _run(tmp_path, WRONG_ACCOUNT_TEXT, video_count=28)

    assert result["identity"]["video_count"] == 28
    assert result["identity"]["nickname"] == "用户0284223142"


def test_login_wrong_account_is_visible_not_hidden(tmp_path):
    """扫错号也要能一眼看出来：identity 里就是别的号。"""
    result = _run(tmp_path, WRONG_ACCOUNT_TEXT, video_count=28)

    assert result["login_status"] == "ok"
    assert result["identity"]["video_count"] != 1


def test_identity_failure_does_not_fail_login(tmp_path):
    result = _run(tmp_path, "登录 微信扫码", video_count=None)

    assert result["login_status"] == "ok"
    assert result["identity"] is None
    assert result["identity_unverified"] is True
    assert "未能确认登录身份" in result["message"]


def test_identity_failure_message_when_no_page_text(tmp_path):
    result = _run(tmp_path, "", video_count=None)

    assert result["identity_unverified"] is True
    assert result["cookies_path"].endswith("cookies.json")


def test_login_result_never_leaks_cookie_values(tmp_path):
    result = _run(tmp_path, RIGHT_ACCOUNT_TEXT)

    assert "SECRET" not in json.dumps(result, ensure_ascii=False)


def test_login_still_writes_0600_with_identity(tmp_path):
    path = tmp_path / "cookies.json"
    _run(tmp_path, RIGHT_ACCOUNT_TEXT)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("text", [WRONG_ACCOUNT_TEXT, RIGHT_ACCOUNT_TEXT])
def test_identity_is_json_serializable(text):
    assert json.loads(json.dumps(parse_identity(text))) == parse_identity(text)
