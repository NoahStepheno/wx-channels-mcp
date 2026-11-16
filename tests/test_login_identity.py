"""v5：`login` 必须回答"登进去的是哪个号"（spec 6.1.1，11-16 登错号事故）。

反例就是这次：`login_status: ok` + cookie 0600 + 两个 cookie 齐全，但号不对，
`post_list` 返 300002，人能看到的只有"成功"。
"""

from __future__ import annotations

import json
import stat

import pytest

from wx_channels_mcp.auth import login as login_mod
from wx_channels_mcp.auth.identity import parse_identity, probe_video_count
from wx_channels_mcp.auth.login import login
from wx_channels_mcp.fetch.errors import WxError

# 11-16 事故现场真实页面文本（headless 读出来的）
# 11-16 事故页面真实排版：`助手 |` 后面直接是昵称，没有序号
WRONG_ACCOUNT_TEXT = (
    "视频号 · 助手 | 用户0284223142 | 申请认证 | 视频号ID: | 视频28 | 关注者0 | "
    "如何让更多的人看到你的视频？ | 2021年6月22日"
)
UNPARSEABLE_TEXT = "登录 微信扫码"
RIGHT_ACCOUNT_TEXT = "视频号 · 助手 | 1 | 小满的视频号 | 视频1 | 关注者0"


def test_parse_identity_real_accident_layout():
    """真实事故排版没有序号：`助手 | 用户0284223142 |` 也要能解析。"""
    assert parse_identity(WRONG_ACCOUNT_TEXT) == {"nickname": "用户0284223142"}


def test_parse_identity_never_invents_video_count():
    """硬伤回归：页面文本里有「视频号ID: … 视频28」和「关注者0」，绝不能猜出 28。"""
    identity = parse_identity(WRONG_ACCOUNT_TEXT)

    assert identity == {"nickname": "用户0284223142"}
    assert "video_count" not in identity


def test_parse_identity_right_account():
    assert parse_identity(RIGHT_ACCOUNT_TEXT) == {"nickname": "小满的视频号"}


def test_parse_identity_includes_finder_id_when_present():
    text = "视频号 · 助手 | 1 | 用户0284223142 | 视频号ID: v2_060000227c00@finder | 视频28"

    identity = parse_identity(text)

    assert identity["finder_id"] == "v2_060000227c00@finder"


def test_parse_identity_missing_nickname_returns_none():
    assert parse_identity("") is None
    assert parse_identity("登录 微信扫码") is None
    assert parse_identity("视频号ID: 视频28 | 关注者0") is None, "只有数字序列不算昵称"


def test_probe_video_count_uses_total_count():
    class Api:
        def __init__(self, payload):
            self.payload = payload

        def list_posts(self, **_kwargs):
            return {"errCode": 0, "data": self.payload}

    assert probe_video_count(Api({"totalCount": 1})) == ("ok", 1)
    assert probe_video_count(Api({})) == ("error", None)


def test_probe_video_count_marks_300002_as_denied():
    class Api:
        def list_posts(self, **_kwargs):
            raise WxError("接口返回 errCode=300002", code=300002)

    assert probe_video_count(Api()) == ("denied", None)


def test_probe_video_count_other_error_is_error():
    class Api:
        def list_posts(self, **_kwargs):
            raise WxError("网络断了")

    assert probe_video_count(Api()) == ("error", None)


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


def _run(tmp_path, text, *, probe=("ok", 1), probe_fn=None):
    session = FakeSession(COOKIES, text)
    return login(
        tmp_path / "cookies.json",
        profile_dir=tmp_path / "p",
        session_factory=lambda _p: session,
        sleep=lambda _s: None,
        state=login_mod.SessionState(tmp_path / "state.json"),
        identity_probe=probe_fn or (lambda _cookies: probe),
    )


def test_login_reports_identity_when_parsed(tmp_path):
    result = _run(tmp_path, RIGHT_ACCOUNT_TEXT, probe=("ok", 1))

    assert result["login_status"] == "ok"
    assert result["identity"] == {"nickname": "小满的视频号", "video_count": 1}
    assert "probe" not in result["identity"], "identity 内不得复制 probe（v6.2）"
    assert result["identity_unverified"] is False
    assert result["probe"] == "ok"
    assert "identity" in json.dumps(result)


def test_login_wrong_account_is_visible_not_hidden(tmp_path):
    """扫错号：identity 里就是别的号（视频数来自接口，不是页面文本猜的）。"""
    result = _run(tmp_path, WRONG_ACCOUNT_TEXT, probe=("ok", 28))

    assert result["login_status"] == "ok"
    assert result["identity"]["video_count"] == 28
    assert result["identity"]["nickname"] == "用户0284223142"


def test_login_denied_probe_says_wrong_account(tmp_path):
    """300002 是最直接的"号不对"信号，必须在 login 输出里看得见。"""
    result = _run(tmp_path, UNPARSEABLE_TEXT, probe=("denied", None))

    assert result["login_status"] == "ok"
    assert result["identity"] is None
    assert result["identity_unverified"] is True
    assert result["probe"] == "denied"
    assert "扫错了微信号" in result["message"]


def test_identity_failure_does_not_fail_login(tmp_path):
    """昵称解析不出但探测正常：已拿到的 video_count 不许丢，identity_unverified=(identity is null)。"""
    result = _run(tmp_path, UNPARSEABLE_TEXT, probe=("ok", 1))

    assert result["login_status"] == "ok"
    assert result["identity"] == {"video_count": 1}
    assert result["identity_unverified"] is False
    assert result["probe"] == "ok"
    assert "message" not in result


def test_identity_probe_error_message(tmp_path):
    result = _run(tmp_path, RIGHT_ACCOUNT_TEXT, probe=("error", None))

    assert result["identity_unverified"] is False, "有昵称就算 identity 非空（探测失败不等于没身份）"
    assert result["probe"] == "error"
    assert "探测失败" in result["message"]


def test_identity_failure_message_when_no_page_text(tmp_path):
    result = _run(tmp_path, UNPARSEABLE_TEXT, probe=("error", None))

    assert result["identity_unverified"] is True
    assert result["cookies_path"].endswith("cookies.json")


def test_probe_is_top_level_only(tmp_path):
    """v6.2 反例④：probe 只在 identity 里出现即不通过。"""
    result = _run(tmp_path, RIGHT_ACCOUNT_TEXT)

    assert result["probe"] == "ok"
    assert set(result["identity"]) == {"nickname", "video_count"}


def test_denied_standard_shape(tmp_path):
    """v6.2：扫错号的标准输出形态（预期结果，不是失败）。"""
    result = _run(tmp_path, UNPARSEABLE_TEXT, probe=("denied", None))

    assert result["login_status"] == "ok"
    assert result["probe"] == "denied"
    assert result["identity"] is None
    assert result["identity_unverified"] is True
    assert "扫错了微信号" in result["message"]


def test_identity_never_contains_null_placeholders(tmp_path):
    """v6.2 反例⑤：不许 nickname: null 这类占位。"""
    result = _run(tmp_path, RIGHT_ACCOUNT_TEXT, probe=("ok", 1))

    assert result["identity"] == {"nickname": "小满的视频号", "video_count": 1}
    assert all(value is not None for value in result["identity"].values())


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


# --- v6.2 缺陷回归：resolve_identity 必须保留"已拿到的字段"，probe 独立 ----------------


@pytest.mark.parametrize(
    ("page_text", "probe", "expected_identity", "expected_probe"),
    [
        # 11-16 事故页面：昵称能解析（放宽后兼容"助手 | 昵称 |"），但号不对 → probe=denied
        (WRONG_ACCOUNT_TEXT, ("denied", None), {"nickname": "用户0284223142"}, "denied"),
        (WRONG_ACCOUNT_TEXT, ("ok", 1), {"nickname": "用户0284223142", "video_count": 1}, "ok"),
        # 页面文本解析不出昵称：只剩探测结果，identity 该 null 就 null
        (UNPARSEABLE_TEXT, ("denied", None), None, "denied"),
        (UNPARSEABLE_TEXT, ("ok", 1), {"video_count": 1}, "ok"),
        (RIGHT_ACCOUNT_TEXT, ("denied", None), {"nickname": "小满的视频号"}, "denied"),
        (RIGHT_ACCOUNT_TEXT, ("ok", 1), {"nickname": "小满的视频号", "video_count": 1}, "ok"),
    ],
)
def test_resolve_identity_keeps_what_it_got(page_text, probe, expected_identity, expected_probe):
    """缺陷 1/3 回归：denied 不许被昵称解析失败带跑偏；已拿到的字段不许丢。"""
    from wx_channels_mcp.auth.login import resolve_identity

    identity, status = resolve_identity(page_text, {}, probe=lambda _c: probe)

    assert identity == expected_identity
    assert status == expected_probe


def test_denied_wins_even_with_nickname(tmp_path):
    """有昵称也可能号不对：probe=denied 必须盖过"看起来拿到了身份"。"""
    result = _run(tmp_path, RIGHT_ACCOUNT_TEXT, probe=("denied", None))

    assert result["probe"] == "denied"
    assert result["identity"] == {"nickname": "小满的视频号"}
    assert result["identity_unverified"] is False
    assert "扫错了微信号" in result["message"]
