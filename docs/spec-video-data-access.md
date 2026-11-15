# 视频号数据接入 · 需求 spec

| 项 | 内容 |
|---|---|
| 产品/需求 | 回声（数据复盘侧） |
| 实现 | 小满（`wx-channels-mcp`，参考 `douyin-mcp`） |
| 验收 | 回声（真机跑通 + 字段口径核对） |
| 状态 | Draft v1（2026-11-15），真机事实待探路确认 |
| 目标平台 | 视频号助手 `channels.weixin.qq.com`（创作者后台） |
| 技术栈 | 与 douyin-mcp 同栈（Python）——Human 2026-11-15 定 |

## 1. 背景与问题

视频号现在**没有自动化数据来源**：每条作品发布后，复盘需要的播放、完播、3 秒留存、涨粉靠人工抄。
抖音侧已有 `douyin-mcp`（PR #9 已合并，评论采集到了 v1），视频号是唯一还在手抄的平台。

缺数据的直接后果（不是"工具缺一个"，是业务判断做不了）：

- 两个平台各自出结论、不合并，视频号没有数就只能凭感觉
- H1 假设（自曝式钱钩子能不能嫁接进 AI 工具实测）判据要视频号 A×2 vs C×2 的 3 秒留存与完播率
- 第 0 层的"按时发 + 记录齐全"守不住，第 1 层就永远启用不了

## 2. 目标与非目标

**目标**：让视频号的作品指标能按固定时间窗口（发布后 +3 / +7 天）自动取到，落到本地可查询的结构，字段口径可核。

**非目标（本期不做）**
- 发布、回复评论、私信等任何**写操作**
- 直播数据、私信数据、商单线索（第二期）
- 情感分析、用户画像、跨平台合并看板
- 不做"全量历史回补"以外的数据清洗（清洗归回声）

## 3. 数据优先级

### P0 作品维度（复盘必需，先做这个）
- 作品列表：标题、发布时间、时长、可见状态
- 指标：播放量、完播率、平均播放时长、点赞、评论数、转发、收藏、涨粉贡献
- 时间窗口：发布后 **+3 天、+7 天**各一份快照

### P1 流量来源构成
推荐 / 关注 / 朋友 / 搜一搜 / 主页 / 其他。视频号来源结构与抖音差异大，缺这块只能给数字、无法归因。

### P2 粉丝
新增关注、取关、来源（有则更好，可后置）

### P3 商单线索（第二期）
评论优先、私信不做；评论 schema 直接复用抖音评论 v1，不另造一套。

## 4. 与 douyin-mcp 的对应关系

**可直接复用（照抄结构与纪律，别重新设计）**
- CLI 骨架：`sync` → `details`，配置走环境变量，数据落 SQLite
- 环境变量约定：`DATA_DIR`、`<PLATFORM>_BROWSER_PROFILE_DIR`、`HEADLESS`
- 状态桶与错误码：`rate_limited` / `cooldown_until` / `login_required` / `parse_failed` / `skipped(+reason)`，判定顺序照抄
- 快照幂等键 `(video_id, snapshot_kind)`、`sampled`/`completeness` 三态思路
- pytest 结构、fixture 标注来源 URL 与抓取日期的做法
- 隐私边界：不采集用户头像/主页链接/user_id，不返回 cookie/header

**不可复用（必须独立）**
- 登录流程、页面结构、DOM 选择器
- **`DATA_DIR` 与 `browser-profile` 必须独立**，两个平台永不共用目录
  - 复用抖音 profile 的后果：单实例锁互撞、覆盖抖音登录态、甚至串号，而症状看起来像"工具坏了"
  - 这条写进 README，不只在 issue 里说

## 5. 数据契约

### 5.1 视频作品表（草案，可调）

| 字段 | 类型 | 说明 |
|---|---|---|
| video_id | TEXT PK | 平台作品 id；派生时记 `id_source` |
| account_id | TEXT NOT NULL | 与抖音侧一致 |
| title | TEXT | 作品标题/文案首行 |
| published_at | TEXT | ISO8601；精度见 `time_precision` |
| time_precision | TEXT | `minute` / `date` |
| published_tz | TEXT | 默认 `Asia/Shanghai` |
| published_raw | TEXT | 页面原始字符串，便于复核 |
| duration_sec | INTEGER | 时长 |
| url | TEXT | 作品链接（可选） |
| visibility | TEXT | `public` / `private` / `deleted` / `unknown` |
| first_seen_at / last_seen_at | TEXT | 采集时间 |

### 5.2 指标快照表

| 字段 | 类型 | 说明 |
|---|---|---|
| video_id | TEXT | |
| snapshot_kind | TEXT | `d3` / `d7` / `backfill` |
| fetched_at | TEXT | 本次抓取时间（UTC） |
| offset_days | REAL | `fetched_at - published_at`，小数天（date 精度时为整数天） |
| on_time | INTEGER | 是否落在目标窗口 |
| snapshot_late | INTEGER | 迟到标记，冻结后不改 |
| play_count | INTEGER | 播放量（**0 是有效值**） |
| finish_rate | REAL | 完播率（0-1 或百分数，写清单位） |
| avg_watch_sec | REAL | 平均播放时长 |
| like_count / comment_count / share_count / favorite_count | INTEGER | 互动四项 |
| follower_delta | INTEGER | 涨粉贡献 |
| period | TEXT NULL | 平台若只给周期统计，写周期口径；否则 NULL |
| completeness | TEXT | `full` / `partial` |
| field_missing | JSON | 本次缺失字段列表 |
| enriched_fields | JSON | `{字段名: 补数时间}`，字段级时点 |
| parser_version | TEXT | |

### 5.3 快照规则（写死，不写死就会静默出错）

1. **基准**：平台发布时间（精确到分钟）。`offset_days = fetched_at - published_at`，保留小数。
2. **窗口**：`d3` = `3 ≤ offset_days < 4`；`d7` = `7 ≤ offset_days < 8`；窗口内 `on_time=1`。
   - `offset_days ≥ target+1` → 仍可记该 kind，但 `snapshot_late=1`
   - `offset_days < target` → **不得**标 `d3/d7`（不拿 +2.6 天冒充 +3）
3. **幂等**：唯一键 `(video_id, snapshot_kind)`；首次成功写入即**冻结数值**，重跑只更新 `last_seen_at`/`fetched_at`，不覆盖已冻结指标；失败不落行、可重试；late 标记同样冻结。
4. **完整性闸门**：核心三件（`video_id` + 可信 `published_at` + `play_count is not None`）缺一 → 不冻结，记 `partial` 在窗口内重试；其余字段允许 null，落 `field_missing`，窗口内**只补空、不改已冻结值**。
5. **缺窗口**：`d3` 到 `d7` 仍未取到 → `missing(window_missed)`，严禁用 d7 回填。
6. **老作品接入**：已过 +7 的记 `backfill` + 实际 offset，不参与 +3/+7 对比。
7. **口径**：完播率/平均时长只用「作品自发布起累计」；平台若只给周期口径，必须带 `period`，且只做同周期比较。
8. **时间精度降级**：只有日期时按自然日算（`day_offset`），`d3` = 恰好第 3 天，不做小数天。

### 5.4 状态与缺失原因（互斥，每快照只有一个 reason）

优先级命中即停：`not_visible` → `unsupported(field_unavailable)` → `not_run` → `parse_failed` → `window_missed`。

- 覆盖率分母 = 应取快照 − `not_visible` − `unsupported`；分子 = on_time/late 中核心字段完整的行
- `unsupported` 需稳定复现依据：同一字段连续 3 次缺失且跨 ≥2 个抓取批次才升级；任一次读到即清零
- 检查未过不删数据：失败样本留着，只标无效

**字段级缺失计数（必须落表，不能只在内存里）**

表 `field_absence`，主键 `(video_id, field)`：

| 字段 | 类型 | 说明 |
|---|---|---|
| video_id | TEXT | |
| field | TEXT | 字段名，如 `play_count` |
| consecutive_missing | INTEGER | 连续缺失次数，读到即清零 |
| first_missing_at | TEXT | 首次缺失时间 |
| last_checked_at | TEXT | 最近一次检查时间 |
| batches_seen | JSON | 出现在哪些抓取批次（批次 id 列表），用于判断"跨批次" |
| status | TEXT | `counting` / `unsupported` |

升级规则：`consecutive_missing ≥ 3` 且 `len(batches_seen) ≥ 2` → `status=unsupported`，同时该快照的 `field_missing` 保留该字段、`reason` 走 `unsupported`（互斥优先级在上表之后）。

## 6. 接口（名字、入参出参先固定；实现细节随探路调整）

命名跟抖音侧对齐：MCP tool 用 `<platform>_browser_<动作>`，CLI 用子命令。

### 6.1 CLI

| 命令 | 入参 | 出参/副作用 |
|---|---|---|
| `wx-channels-mcp sync` | `--recent-limit N`（默认 20）、`--headless/--headed` | 作品列表 upsert 到 `videos`；对当天到期作品取快照，写 `snapshots` |
| `wx-channels-mcp details` | `--video-id` 或 `--recent-limit N` | 单/多作品指标详情，写 `snapshots`（不重复取已冻结的 kind） |
| `wx-channels-mcp export` | `--format csv`（默认）、`--since`、`--out` | 导出快照 CSV，列与复盘记录表对齐 |

### 6.2 MCP tools（如果没有 MCP 通道，CLI 先跑通，tool 后补）

**`wx_browser_sync_channel_videos`**
- 入参：`limit`（int，默认 20，上限 50）
- 出参：`{status, fetched_at, login_status, videos: [{video_id, title, published_at, time_precision, duration_sec, visibility}], skipped: [{reason}]}`
- `status` 取 `ok` / `login_required` / `rate_limited` / `parse_failed` / `skipped`

**`wx_browser_get_channel_snapshots`**
- 入参：`video_id`（必填）、`kind`（`d3` / `d7` / `auto`，默认 `auto`）、`force`（bool，默认 false，**不得**覆盖已冻结数值）
- 出参：`{status, fetched_at, snapshot: {snapshot_kind, offset_days, on_time, snapshot_late, period, completeness, field_missing, enriched_fields, play_count, finish_rate, avg_watch_sec, like_count, comment_count, share_count, favorite_count, follower_delta}, reason, cooldown_until}`
- 重复调用同一 `(video_id, kind)`：返回 `status=completed` + `idempotent_hit=true`，不产生第二行

**`wx_browser_export_snapshots`**
- 入参：`fmt`（`csv`）、`since`（ISO 日期，可选）
- 出参：`{status, path, rows}`

### 6.3 错误码

复用抖音侧：`login_required` / `rate_limited` / `cooldown_until` / `parse_failed` / `skipped(reason)`；
新增 reason 值（不新增桶）：`wx_auth_in_client`（登录只能在微信客户端完成）、`field_unavailable`（平台不提供该字段）、`not_visible`（作品删除/私密）。

## 7. 探路阻塞项（必须先由 Human 在真号确认，否则写了也是猜）

1. **导出**：后台有没有导出？粒度到不到单条作品？→ 能到单条，P0 可能整个采集器都不用做
2. **登录形态**（硬阻塞）：扫码（Playwright 可做）还是微信内授权（只能在微信客户端点 → 采集器方案判死，走导出）
3. **可读页清单**：数据助手有哪些页；哪些指标页面直出、哪些要点进二级页；完播率是否随统计周期变
4. **账号主体**：与抖音是否同一人运营、是否已认证（影响可见数据范围）
5. **独立 profile 同意**：首次扫码由 Human 完成

止损线：导出粒度不到单条 + 登录只能微信内授权 → 直接回人工导出 + 字段映射，不投入真机探路。

## 8. 验收标准（回声跑，结果贴回 MR）

- [ ] 探路产出：页面清单 + 1 条真实样例 JSON + 截图（不写采集器先）
- [ ] `sync` 能落盘作品列表，`published_at` 与页面一致（时间精度、时区都要标）
- [ ] 同一天重复跑：不产生第二条 `(video_id, snapshot_kind)`；已冻结数值不变
- [ ] `offset=2.9` 不标 d3；`3.0` on_time；`4.0` late 且 `snapshot_late=1`
- [ ] `play_count=0` 能冻结，`completeness != partial`
- [ ] 遇验证/风控：`rate_limited`/`cooldown_until` 可读，不抛异常，不重试轰炸
- [ ] `unsupported` 与 `parse_failed` 不会出现在同一次抓取
- [ ] 缺字段时 `field_missing` 有内容；补数时 `enriched_fields` 记录字段级时间
- [ ] 导出 CSV 能被回声直接映射进 `reviews/2026-11-15_记录表.csv`（列对得上）
- [ ] 不返回 cookie/header，不采集头像/主页链接/user_id
- [ ] 每个工具的入参/出参与第 6 节一致（字段名不改，除非先改 spec）
- [ ] `field_absence` 表能查到计数与批次列表；连续 3 次跨 2 批次才升级 `unsupported`；读到一次后计数归零

## 9. 里程碑

1. **探路**（阻塞于第 7 节）→ 页面清单 + 样例 JSON + 截图
2. **定 schema / ticket**（本文档第 5 节定稿）
3. **实现 + pytest**（照抖音侧结构）
4. **review**
5. **Human merge**
6. **回声真机验收**（第 8 节全绿才算过）

## 10. 附：为什么这些字段（验收时对照用）

- 播放量 + 完播率 + 3 秒留存 → 决定"开头能不能留住人"，是 A/C 对照的核心
- 平均播放时长 → 区分"看完"与"划走"
- 涨粉 → 第 2 层目标（视频号看主页访问率 + 咨询数）的前置
- 流量来源 → 归因"这条为什么成了"，没有它只能给结论不能给原因
- period → 防止跨统计周期比较造成的假趋势
