"""命令行入口：`wx-channels-mcp sync|details|export`。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wx_channels_mcp.config import DEFAULT_LOOKBACK_DAYS, DEFAULT_PAGE_SIZE, PAGE_SIZE_MAX
from wx_channels_mcp.fetch.client import WxApi
from wx_channels_mcp.services.export import export_csv
from wx_channels_mcp.services.sync import sync
from wx_channels_mcp.storage.db import connect

DEFAULT_COOKIES_PATH = ".botharness/cookies.json"
DEFAULT_DB_PATH = "data/wx-channels.db"


def _load_cookies(path: str) -> dict[str, str]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return {k: str(v) for k, v in raw.items()}
    # Playwright 导出的 [{name, value, ...}] 也能吃
    return {item["name"]: item["value"] for item in raw}


def _cmd_sync(args) -> int:
    api = WxApi(_load_cookies(args.cookies))
    conn = None if args.dry_run else connect(args.db)
    summary = sync(
        api,
        conn,
        dry_run=args.dry_run,
        lookback_days=args.lookback_days,
        page_size=args.page_size,
        json_path=args.json,
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

    p_sync = sub.add_parser("sync", help="拉取近期作品并落快照")
    p_sync.add_argument("--dry-run", action="store_true", help="只取数/分桶并打印将要落库的行，不写库")
    p_sync.add_argument("--json", default=None, help="把本次运行摘要写到指定文件")
    p_sync.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    p_sync.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, choices=range(1, PAGE_SIZE_MAX + 1))
    p_sync.add_argument("--cookies", default=DEFAULT_COOKIES_PATH, help="cookie JSON（name/value 列表或字典）")
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
