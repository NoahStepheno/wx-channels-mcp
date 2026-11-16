"""命令行入口：`wx-channels-mcp login|sync|export`。"""

from __future__ import annotations

import argparse
import json
import sys

from wx_channels_mcp.auth.login import DEFAULT_PROFILE_DIR, login
from wx_channels_mcp.auth.session import (
    DEFAULT_COOKIES_PATH,
    DEFAULT_LOGIN_STATE_PATH,
    SessionState,
    read_cookies,
)
from wx_channels_mcp.config import DEFAULT_LOOKBACK_DAYS, DEFAULT_PAGE_SIZE, PAGE_SIZE_MAX
from wx_channels_mcp.fetch.client import WxApi
from wx_channels_mcp.fetch.errors import LoginRequired
from wx_channels_mcp.services.export import export_csv
from wx_channels_mcp.services.sync import sync
from wx_channels_mcp.storage.db import connect

DEFAULT_DB_PATH = "data/wx-channels.db"


def _cmd_login(args) -> int:
    try:
        result = login(
            args.cookies,
            profile_dir=args.profile,
            session_factory=None,
            state=SessionState(DEFAULT_LOGIN_STATE_PATH),
            timeout_seconds=args.timeout,
        )
    except LoginRequired as exc:
        print(json.dumps({"login_status": "login_required", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_sync(args) -> int:
    api = WxApi(read_cookies(args.cookies))
    conn = None if args.dry_run else connect(args.db)
    summary = sync(
        api,
        conn,
        dry_run=args.dry_run,
        lookback_days=args.lookback_days,
        page_size=args.page_size,
        json_path=args.json,
        session_state=SessionState(args.state),
    )
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    return 0 if summary.status == "ok" else 2


def _cmd_export(args) -> int:
    conn = connect(args.db)
    rows = export_csv(conn, args.out, since=args.since)
    print(json.dumps({"status": "ok", "path": args.out, "rows": rows}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wx-channels-mcp")
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="扫码登录并写入 cookies.json（0600），登录由 Human 完成")
    p_login.add_argument("--cookies", default=DEFAULT_COOKIES_PATH)
    p_login.add_argument("--profile", default=DEFAULT_PROFILE_DIR, help="本平台独立浏览器 profile 目录")
    p_login.add_argument("--timeout", type=float, default=300.0, help="等待扫码秒数")
    p_login.set_defaults(func=_cmd_login)

    p_sync = sub.add_parser("sync", help="拉取近期作品并落快照")
    p_sync.add_argument("--dry-run", action="store_true", help="只取数/分桶并打印将要落库的行，不写库")
    p_sync.add_argument("--json", default=None, help="把本次运行摘要写到指定文件")
    p_sync.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    p_sync.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, choices=range(1, PAGE_SIZE_MAX + 1))
    p_sync.add_argument("--cookies", default=DEFAULT_COOKIES_PATH, help="cookie JSON（name/value 列表或字典）")
    p_sync.add_argument("--state", default=DEFAULT_LOGIN_STATE_PATH, help="登录态时间线（判定 300333 用）")
    p_sync.add_argument("--db", default=DEFAULT_DB_PATH)
    p_sync.set_defaults(func=_cmd_sync)

    p_export = sub.add_parser("export", help="导出本地快照 CSV（与平台下载无关）")
    p_export.add_argument("--out", default="data/snapshots.csv")
    p_export.add_argument("--since", default=None, help="ISO 日期，按发布时间过滤")
    p_export.add_argument("--db", default=DEFAULT_DB_PATH)
    p_export.set_defaults(func=_cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
