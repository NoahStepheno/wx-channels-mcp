# wx-channels-mcp

视频号（`channels.weixin.qq.com`，微信视频号助手）数据接入 MCP 工具，给数据复盘用。
参照 [`douyin-mcp`](https://github.com/NoahStepheno/douyin-mcp) 的结构与纪律，技术栈同栈（Python）。

- 需求/验收基线：`docs/spec-video-data-access.md`（冻结于 git tag `acceptance-v1` / `acceptance-v2`）
- 需求 issue：<https://github.com/NoahStepheno/wx-channels-mcp/issues/1>
- 产品与验收：回声；实现：小满；合并：Human

## 当前状态

探路阶段（2026-11-15）：登录形态、数据来源已确认，页面与接口清点进行中。
已落地：`src/wx_channels_mcp/rules/window.py`（+3/+7 快照窗口分桶，纯函数，带单测）。

## 快速开始

```bash
# 1. 首次 clone 后启用仓库卫生钩子（拦截 .botharness/、Cookies、*.pyc 等入库）
git config core.hooksPath .githooks

# 2. 安装（Python >= 3.11）
python -m venv .venv && . .venv/bin/activate
pip install -e .

# 3. 跑测试
PYTHONPATH=src pytest -q
```

## 硬约定

- **视频号独立 `DATA_DIR` 与独立 `browser-profile`，与抖音侧永不共用目录。**
  复用抖音的 profile 会单实例锁互撞、覆盖抖音登录态、甚至串号，而症状看起来像"工具坏了"。
- 只读采集：不发布、不回复、不私信、不改设置。
- 登录态、浏览器 profile、探路原始产物一律落在 `.botharness/`，**不入库**（`.gitignore` + pre-commit 钩子双重保障）。
- 比率类指标在库内统一存 **0–1 小数**，展示层再转百分数，不混存两套单位。
- 平台数据依赖登录态与页面/内部接口结构，变化要有兜底：字段缺失走 `parse_failed`，不静默写 0。
