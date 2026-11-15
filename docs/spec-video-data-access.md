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

### P0.5 留存曲线（可能替掉「3 秒留存」的位置）
`get_feed_lost_rate({feedId})` → 组件 `PostChurnRate`「观众留存趋势」，formatter `(100*t).toFixed(0)+"%"`。**这是后台"留存"曲线的真实来源，与 `fastFlipRate` 是两个东西，不许混用。**
若曲线按秒/按等分桶返回，则 3 秒留存可由累积流失反推（`keep_rate_3s = 1 - cum_churn(3s)`），该字段就能落成有出处的派生值，替掉长期挂着的 `pending_unit_check`。**探路只需回答两件事**：桶的粒度（秒/百分比/十等分）、`t` 是"流失率"还是"留存率"。答案拿到前不写实现。

### P1 流量来源构成
推荐 / 关注 / 朋友 / 搜一搜 / 主页 / 其他。视频号来源结构与抖音差异大，缺这块只能给数字、无法归因。
数据源尝试过：`POST /statistic/feed_aggreagate_data_by_tab_type`，入参 `{feedId, startTs, endTs, interval: Day}`。**2026-11-15 实测两轮全挂**（`300999 request failed`），试过 `interval=3`（`gx.Day` 的实际值）+ 秒/毫秒时间戳、`feedId` 带/不带 `export/` 前缀、补/不补 `_log_finder_id`，均失败。**结论：P1 记为 `skipped(no_source_field)`，停止猜参。**
留下的线索（下次再碰时先看这三条，别重头来）：
- bundle 里来源维度取的是 **`dataByFanstype`**（`sourceData = t.dataByFanstype`），不是 `dataByTabtype`——字段名可能一直找错了
- 同一接口大概率还要 `busiType` 或 iframe 会话上下文（缺的是环境，不是参数名）
- 交叉验证入口：`new_post_total_data` 里若带来源维度，可先拿它当 P1 的低保真替代

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
9. **late 快照不可与 on_time 混比（2026-11-15 补，回声）**：`offset_days` 越大，累计播放越多，6.99 天的 d3 和 3.1 天的 d3 不是同一个东西。因此：
   - `late=1` 的行**照常落库、照常显示**（不删数据），但**默认不进基准线与任何跨条对比**；
   - 确需使用（样本不足）时，必须同行记录 `offset_days`，并只与 `offset_days` 相差 ≤0.5 天的快照比较，且在复盘里注明"含迟到快照"；
   - 基准线只用 `on_time=1` 的行。这条与「不删数据」不冲突：留档 ≠ 可混比。
10. **`replay_suspected` 分两级处理（2026-11-15 补，回声）**：重播是**结果**，不是**误差**——它是钩子有效的证据之一。把它整类踢出基准线，等于系统性地删掉表现最好的样本，基准线被压低，之后所有作品都会显得像爆款。因此拆成两个判据：
    - `is_baseline_eligible`（进基准线）：允许 `replay_suspected=1`，仍要求 `on_time=1`、锚点通过、非 `unit_mismatch`。基准线统计必须同时报「含重播 n / 不含重播 n」两个数，差值大就说明重播集中在少数作品。
    - `is_compare_eligible`（A/C 对比、跨条排名）：额外排除 `replay_suspected=1`，因为重播量不同会让留存类指标不可比。
    - 一句话：**进基准线可以，进对比不行。**

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

## 5.5 数据来源与口径（2026-11-15 探路后）

**主路径：同源 JSON 接口**（前端自己用的，带登录 cookie）
- `cgi-bin/mmfinderassistant-bin/post/post_list`：作品列表 + 单条指标（`readCount` / `fullPlayRate` / `avgPlayTimeSec` / `fastFlipRate` / `likeCount` / `commentCount` / `forwardCount` / `favCount` / `followCount` / `cancelFollowCount` / `yesterdayReadCount` / `createTime` / `visibleType` / `status`）。入参 `{pageSize, currentPage, sort, order, startTime, endTime}`——**支持区间筛 + 排序 + 翻页**
- `statistic/new_post_total_data`：按 tabType 的汇总
- `statistic/fans_trend`：粉丝增/减/净增/总量
- `statistic/feed_aggreagate_data_by_tab_type`：单条来源构成（P1）
- `get_feed_lost_rate`：留存曲线（P0.5，见 §3）
- `POST /statistic/download_post_data`：**「导出」= 前端把接口 JSON 在浏览器里拼表**，不是平台独立口径（见下「交叉校验」）

**P0 拉取策略（写死）**：**窗口分桶在本地算，不信服务端筛。** 服务端 `startTime/endTime` 只当传输优化（少翻页），`+3/+7` 的快照归属一律用本地 `published_at` 与 `offset_days` 判定（见 §5.3）。原因：服务端区间是"发布时间落在区间内"，而快照要的是"这条作品现在距发布 N 天"——两者不是一回事，混用就会在跨窗口边界上悄悄错桶。

**「导出」不是独立口径（2026-11-15 实测，推翻 v2 的假设）**：`POST /statistic/download_post_data` 返回 `errCode:0` + `data.list`，里面是**和 `post_list` 一模一样的作品对象**。也就是说前端的"下载"是本机把 JSON 拼成表，平台没有给我们另一份口径。**推论：不能用导出做交叉校验**，那等于自己跟自己比，只会稳定通过、什么也证明不了。导出能力**只保留接口、不实现文件解析**。

**交叉校验改为两级锚点（写死，替掉 v2 的「导出比对」）**
- **锚点 A（必做，Human 每批一次）**：人类本来就要手报数字（+3/+7 各报 3 个数）。用同一作品的**手抄值**当锚，接口值 vs 手抄值，比率类相对误差 ≤2%；超差不采信、记 `unit_mismatch`。这是唯一真正独立于我方代码的证据，且不额外增加人类负担。
- **锚点 B（自动，每次同步免费做）**：跨接口内部一致性——`avgPlayTimeSec ≤ duration`；`fullPlayRate` 与 `avgPlayTimeSec/duration` 同向不悖；若 P0.5 留存曲线可用，则曲线积分 ≈ `avgPlayTimeSec`、末端 ≈ `fullPlayRate`。不满足即 `unit_mismatch`，**不猜哪个对，先停用该字段**。
- 两条都过才算「口径可信」；只有 B 过、A 缺失 → 字段标 `pending_human_anchor`，可落库但不进 H1。
- **校验状态可升级、指标值不可改**：先 `anchor_unverified=1`、后来补齐 `duration` 得出确定结论，允许改写 `anchor_status`/`anchor_unverified`（这是校验状态，不是指标），但必须留 `anchor_checked_at` 与 `anchor_evidence`（哪几个数算的），否则事后无法解释某个字段为什么被停用。
- **`insufficient_data` 不算 B 通过**：缺时长或均播时校验无从做起（如 `duration` 缺失），必须显式落 `anchor_unverified=1`，不许因为"没报错"就当通过——这是最容易静默放行的路径。

**登录态就是两个 cookie（2026-11-15 实测）**：`channels.weixin.qq.com` 登录后只需 `sessionid` + `wxuin`，纯 Python `urllib` POST **不启浏览器**即可 `errCode:0` 取数。所以架构按「登录层只产出 cookie / 取数层是普通 HTTP 客户端」解耦，可单测、可 mock。**`sessionid` 是 bearer 凭证，等价于账号密码**：不落库、不写日志、不进样例 JSON、不出现在异常栈里（异常包装 `SESSION_COOKIE=<redacted>`）；`DATA_DIR` 与 `browser-profile` 与抖音**永不共用**。

**降级路径**：DOM 直读不做主路径（SPA + iframe + canvas 渲染，太脆；单篇数据页 `statistic/postDetail` 直接访问 URL 不渲染，只能经菜单进，一律走 API）。

**口径约定（写死）**
1. 比率一律存 **0–1 小数**，行上带 `rate_unit="ratio"`；展示层转百分数。人类手报的百分数入表前统一换算，禁止两套单位混存。
2. `fastFlipRate` 的字面是「快速划走率」，**它是不是页面上的「3 秒留存」尚未证实**。落库策略（不变量：原始值永远不改名、不换算）：
   - 存原始值 `fast_flip_rate`；派生值 `keep_rate_3s` 与 `rate_derivation` 分开存
   - 标签证据为「快划率」→ `keep_rate_3s = 1 - fast_flip_rate`，`rate_derivation="1-fastFlipRate"`
   - 标签证据为「3 秒留存」→ `keep_rate_3s = fast_flip_rate`，`rate_derivation="direct"`
   - 证据到位前：`keep_rate_3s = NULL`、`pending_unit_check=true`；**不得进 H1 对比，也不得触发 40% 告警**
   - **证据现状（负结论，已穷尽）**：`fastFlipRate` 在两个主 bundle（`micro/content/iframe/post-card` 7.4MB、`micro/statistic/post` 5.2MB）里 **0 命中**；`3秒`/`三秒`/`秒内`/`快划`/`划走` 同样 0 命中；唯一绑定的中文标签是 `fullPlayRate ↔ 完播率`。**结论：这是接口返回、列表页不展示的字段，UI 侧不存在可指名的中文标签。** 因此 (a)(b)(c) 三条证据路径全部失效，不再重复搜 bundle/列表页；只剩单篇详情页一次机会，找不到就长期 `pending_unit_check`。
   - **但留存口径另有出处**：后台的「观众留存趋势」走 `get_feed_lost_rate` / 组件 `PostChurnRate`，与 `fastFlipRate` 无关。`fastFlipRate` 大概率就停在"原始值存档、不解释"的位置，3 秒留存改由 §3 P0.5 的曲线提供（若曲线粒度够）。别再为了这个字段耗探路预算。
   - 算术旁证（样例：162s 视频、574 播放、均播 23.79s、完播 5.92%）：若 0.336 是留存，通过 3 秒的 193 人需平均看 64.8s（占时长 40%）且其中 17.6% 看完；若是快划率，通过 3 秒的 381 人平均 34.3s、8.9% 看完——后者更符合 162s 长视频的常见形态。**旁证不作数，只解释为什么卡这个字段。**
3. `createTime` 是 Unix 秒，落库转 ISO8601 + 保留原值；时区按 `Asia/Shanghai`。
4. **重播（loop）会让均播 > 时长，这不是单位错。** 锚点 B 的判法分三档，避免把正常作品误判成 `unit_mismatch` 而丢数据：
   - `avg_play_time_sec <= duration_sec * 1.05` → 正常
   - `duration_sec * 1.05 < avg_play_time_sec <= duration_sec * 1.5` → 记 `replay_suspected=1`，**不停字段**，但该作品不进 H1 判据
   - `avg_play_time_sec > duration_sec * 1.5` → 判 `unit_mismatch`，停用该字段（这个倍数只能来自重播之外的错误，比如时长单位是毫秒）
   - 同理 `fullPlayRate <= avg_play_time_sec / duration_sec + 容差` 是恒真约束（完播的人必然贡献满时长），违反才是真异常。

**风险登记**
- 同源接口**未公开、无文档**，随时可能改版/加签名参数。缓解：`parser_version` 记录、字段缺失走 `parse_failed` 而非静默 0、每作品每日最多 1 次调用。
- 若导出存在且粒度到单条 → 仍以 API 为主，导出仅用于交叉校验（不做采集实现）。

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

1. **导出**（**已结案，2026-11-15 实测**）：后台有"下载"按钮，但走 `POST /statistic/download_post_data` 返回的是 `post_list` 同一份 JSON，前端在本机拼表。**平台没有独立口径的导出文件**，因此不采信为交叉校验来源，只保留接口能力、不做文件解析。架构维持「API 直连」，不再考虑「点导出 → 解析文件」
2. **登录形态**（硬阻塞）：**已确认＝扫码（Playwright 可做）**，2026-11-15 小满实测 `channels.weixin.qq.com/login.html` 内嵌 `open.weixin.qq.com/connect/qrconnect?...&login_type=jssdk` 标准扫码 iframe（208×208）。止损线第 2 条不触发，采集器方案成立。仍需 Human 扫码一次以建立独立 profile
3. **可读页清单**：数据助手有哪些页；哪些指标页面直出、哪些要点进二级页；完播率是否随统计周期变
4. **账号主体**：与抖音是否同一人运营、是否已认证（影响可见数据范围）
5. **独立 profile 同意**：首次扫码由 Human 完成

止损线：导出粒度不到单条 + 登录只能微信内授权 → 直接回人工导出 + 字段映射，不投入真机探路。

## 8. 验收标准（回声跑，结果贴回 MR）

> **验收基线 v4（冻结）**：以本文件 `acceptance-v4` tag 为准。issue 作承载与讨论，不构成基线；任何验收标准变更必须升版本并在本节写 changelog。
>
> **changelog**
> - v1（`acceptance-v1`，1204b20）：12 条基础验收
> - v2（2026-11-15）：+2 条。原因：探路发现真实数据源是同源 JSON 接口而非页面，必须增加「API 值 vs UI/导出抽样交叉校验」与「脱敏检查」，否则接口字段单位或语义漂移会静默污染复盘
> - v3（2026-11-15）：把「口径证据」从"必须 UI 截图"放宽为「UI 截图 / 前端 bundle 标签映射 / 页面 DOM 可见文本，三者任一，须可指名来源」。原因：数据页是 canvas 渲染、`innerText` 为空，截图路径不稳，Human 也不该被当成唯一取证工具；同时要求原始值不改名、派生值单列
> - v4（2026-11-15）：①`fastFlipRate` 口径调查结案为**负结论**（两主 bundle 0 命中），不再要求取证，改为验收「原始值存档 + `keep_rate_3s` 为 NULL + `pending_unit_check` 为 true」；②新增 P0.5 留存曲线（`get_feed_lost_rate`）与 P1 来源接口（`feed_aggreagate_data_by_tab_type`）的定位，二者**不阻塞 P0**；③新增「窗口分桶必须在本地算，不信服务端筛」验收项；④**导出被证伪为独立口径**（`download_post_data` 返回的仍是 `post_list` 同一份 JSON，前端本机拼表），v2 的「导出交叉校验」替换为两级锚点：A＝Human 手报值（≤2%）＋ B＝跨接口内部一致性；导出能力只保留接口、不实现解析；⑤登记实测事实「登录态只需 `sessionid`+`wxuin`，纯 HTTP 可取数」，并把 `sessionid` 定为 bearer 凭证、禁落库禁日志

- [ ] 探路产出：页面清单 + 1 条真实样例 JSON + 截图（不写采集器先）
- [ ] `sync` 能落盘作品列表，`published_at` 与页面一致（时间精度、时区都要标）
- [ ] 同一天重复跑：不产生第二条 `(video_id, snapshot_kind)`；已冻结数值不变
- [ ] `offset=2.9` 不标 d3；`3.0` on_time；`4.0` late 且 `snapshot_late=1`
- [ ] `play_count=0` 能冻结，`completeness != partial`
- [ ] 遇验证/风控：`rate_limited`/`cooldown_until` 可读，不抛异常，不重试轰炸
- [ ] `unsupported` 与 `parse_failed` 不会出现在同一次抓取
- [ ] 缺字段时 `field_missing` 有内容；补数时 `enriched_fields` 记录字段级时间
- [ ] `wx-channels-mcp export` 产出的 CSV 能被回声直接映射进 `reviews/2026-11-15_记录表.csv`（列对得上）——**导出指的是本工具的 CSV 输出，与平台"下载"无关**
- [ ] 不返回 cookie/header，不采集头像/主页链接/user_id
- [ ] 每个工具的入参/出参与第 6 节一致（字段名不改，除非先改 spec）
- [ ] `field_absence` 表能查到计数与批次列表；连续 3 次跨 2 批次才升级 `unsupported`；读到一次后计数归零
- [ ] **交叉校验（v4 两级）**：锚点 A——至少 1 条作品接口值 vs Human 手报值，比率类相对误差 ≤2%，超差记 `unit_mismatch`；锚点 B——`avgPlayTimeSec ≤ duration`、`fullPlayRate` 与均播/时长不悖，任一不满足即停用该字段
- [ ] **导出不得被当作独立口径**：代码里不存在"读平台导出文件做校验"的路径；若有人加，验收直接打回
- [ ] **脱敏**：样例与落库行不含 cookie / `uin` / token / 头像 / 主页链接 / user_id
- [ ] **比率单位**：库内一律 0–1 小数且带 `rate_unit`；展示层百分数换算正确（`0.0592` → `5.9%`）
- [ ] **`fastFlipRate` 处理（v4 改判）**：不再要求标签证据（两主 bundle 0 命中，已负结论结案）。验收改为——原始值 `fast_flip_rate` 与接口值逐位一致；`keep_rate_3s` 为 NULL；`pending_unit_check` 为 true；且**没有**任何代码路径把它喂进 H1 对比或 40% 告警
- [ ] **窗口分桶在本地**：给 `post_list` 带 `startTime/endTime` 与不带，同一批作品算出的 `snapshot_kind` 必须完全一致；服务端筛只影响翻页次数
- [ ] **P0 不依赖 P1/P0.5**：`feed_aggreagate` / `get_feed_lost_rate` 全失败时，`sync` 仍能完整落 P0 的作品与 +3/+7 快照，缺项记 `skipped(no_source_field)`
- [ ] **留存曲线粒度已查明**（P0.5）：能说出桶的单位与 `t` 的语义，或明确记为"粒度不足、不可反推 3 秒留存"

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
