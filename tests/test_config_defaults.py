"""同步默认值：默认只拉近 30 天（别第一天就扫全历史触发风控）。"""

from wx_channels_mcp.config import DEFAULT_LOOKBACK_DAYS, DEFAULT_PAGE_SIZE, PAGE_SIZE_MAX


def test_default_lookback_is_30_days():
    assert DEFAULT_LOOKBACK_DAYS == 30


def test_page_size_defaults_and_cap():
    assert DEFAULT_PAGE_SIZE == 20
    assert PAGE_SIZE_MAX == 50
    assert DEFAULT_PAGE_SIZE <= PAGE_SIZE_MAX
