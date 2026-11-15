"""同步默认配置。"""

# sync 默认只拉近 30 天：第一天就把全历史扫一遍会招风控（acceptance-v4 / spec §3）
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_PAGE_SIZE = 20
PAGE_SIZE_MAX = 50
