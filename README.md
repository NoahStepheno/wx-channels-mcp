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

# 4. 登录（登录态会过期，过期后重跑这一条即可；扫码由人做，落盘由工具做）
wx-channels-mcp login   # 扫码成功后写 .botharness/cookies.json（0600），并打印登进去的是哪个号

# 5. 取数 / 导出
wx-channels-mcp sync --dry-run   # 只看将要落库的行
wx-channels-mcp sync
wx-channels-mcp export --out data/snapshots.csv
```

`sync` 返回 `login_required` 时会给出 `next_action`（就是第 4 步那条命令）；只有重新扫码后**仍然** `errCode=300333` 才降为 `parse_failed`。

**`sync --dry-run` 不是绝对只读**：遇到 `300333` 时它仍会刷新 `data/login_state.json` 的 `last_300333_at`（诊断时间线，不是内容数据），`login_at` 不动。排查时别以为 dry-run 什么文件都不碰。

**登录成功不等于号对。** `login` 会输出 `identity`（`nickname` + `video_count`，取得到再带 `finder_id`）和**顶层** `probe`（`ok`/`denied`/`error`，`identity` 里不复制）：`video_count` **只**取自 `post_list.totalCount`，页面文本只给昵称（页面里有「视频号ID: …」和「关注者0」，从文本猜视频数会把 0 作品的号报成 28）。`probe: denied` 表示该账号对该接口无权限（300002），基本就是扫错号；身份拿不到时 `identity: null` + `identity_unverified: true`，登录本身仍算成功。跑 `sync` 前先看 `identity.video_count` 是不是你要复盘的那个号。两个错误码含义不同，别混：
- `300333` → `login_required`：会话过期，重扫即可；
- `300002` → `parse_failed`：会话有效但**该账号没有这个接口的权限**（号不对），重扫前先确认扫的是哪个视频号。

## 硬约定

- **视频号独立 `DATA_DIR` 与独立 `browser-profile`，与抖音侧永不共用目录。**
  复用抖音的 profile 会单实例锁互撞、覆盖抖音登录态、甚至串号，而症状看起来像"工具坏了"。
- 只读采集：不发布、不回复、不私信、不改设置。
- 浏览器 profile、探路原始产物落在 `.botharness/`；cookie 在 `.botharness/cookies.json`，登录态时间线在 `data/login_state.json`；目录与文件**都不入库**（`.gitignore` + pre-commit 钩子双重保障）。
  登录态本来跨两个目录，别把它们合并到一个目录里（合并会重现 300333 误判的路径分裂）。
- 比率类指标在库内统一存 **0–1 小数**，展示层再转百分数，不混存两套单位。
- **登录态只有一个来源：默认 `data/login_state.json`。** `login` 与 `sync` 的默认值本来就一致；真机日常运行**不要**给 `sync` 传 `--state`（那是给测试/迁移用的），否则会出现"login 写 `data/`、sync 读别处"的路径分裂，导致 300333 误判成 `parse_failed`。
- 平台数据依赖登录态与页面/内部接口结构，变化要有兜底：字段缺失走 `parse_failed`，不静默写 0。
