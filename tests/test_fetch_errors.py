"""取数层失败分类：三种失败必须可区分，HTML 不能被当 JSON 静默吃掉。"""

import pytest

from wx_channels_mcp.fetch.client import classify_failure
from wx_channels_mcp.fetch.errors import LoginRequired, ParseFailed, RateLimited, WxError


@pytest.mark.parametrize(
    ("status", "content_type", "text", "expected"),
    [
        (429, "application/json", "{}", "rate_limited"),
        (401, "application/json", "{}", "login_required"),
        (403, "text/html", "<html></html>", "login_required"),
        (200, "text/html", "<html><title>微信扫码登录</title></html>", "login_required"),
        (200, "text/html", "<html><body>502 Bad Gateway</body></html>", "parse_failed"),
        (200, "text/html", "<html><title>login</title></html>", "login_required"),
        (500, "application/json", "{}", "parse_failed"),
        (200, "application/json", '{"errCode":0}', None),
    ],
)
def test_classify_failure(status, content_type, text, expected):
    assert classify_failure(status, content_type, text) == expected


@pytest.mark.parametrize(
    ("exc_class", "reason"),
    [(RateLimited, "rate_limited"), (LoginRequired, "login_required"), (ParseFailed, "parse_failed")],
)
def test_errors_expose_state_bucket(exc_class, reason):
    err = exc_class("boom")

    assert isinstance(err, WxError)
    assert err.reason == reason
    assert err.readable.startswith(reason)
