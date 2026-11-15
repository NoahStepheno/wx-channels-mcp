> GitHub Issue 草稿（回声起草）。gh token 当前失效，请 Human 或小满创建正式 issue，MR 挂它。

## 背景

视频号是唯一还要手抄数据的平台。复盘需要发布后 +3 / +7 天的播放、完播、3 秒留存、涨粉；手抄既慢又容易漏，直接卡住两个号的对照结论和 H1 假设的判定。

## 要做什么

1. 按固定窗口（+3 / +7 天）自动取视频号作品指标并落盘
2. 导出 CSV 能被复盘记录表直接映射
3. 状态桶与抖音侧一致，报表层只需一套状态机

## 验收标准

完整清单见 `docs/spec-video-data-access.md` 第 8 节。核心几条：

- [ ] 探路先出页面清单 + 1 条样例 JSON + 截图（不写采集器）
- [ ] `offset=2.9` 不标 d3、`3.0` on_time、`4.0` late 且 `snapshot_late=1`
- [ ] 同日重跑不产生第二条 `(video_id, snapshot_kind)`，已冻结数值不变
- [ ] `play_count=0` 可冻结，`completeness != partial`
- [ ] 风控/验证返回 `rate_limited`/`cooldown_until`，不抛异常、不重试轰炸
- [ ] `unsupported` 与 `parse_failed` 不同时出现；字段缺失有 `field_absence` 计数
- [ ] 不返回 cookie/header，不采集头像/主页链接/user_id
- [ ] 回声真机跑通（第 8 节全绿）才算过

## 边界（不做）

发布/回复/私信等写操作、直播数据、私信、情感分析、跨平台合并看板。商单线索第二期，且只做评论。

## 硬约定

- 独立 `DATA_DIR` + 独立 `browser-profile`，两平台永不共用目录（复用会锁互撞、覆盖抖音登录态、甚至串号）
- 登录态与抖音分文件、分锁
- 技术栈默认 Python（与 douyin-mcp 同栈，迁移成本最低）；若 Human 改 TS/Node，本条作废但不影响验收标准

## 阻塞项（真机确认后回填 spec）

1. 后台能否导出、粒度到不到单条
2. 登录形态：扫码（Playwright 可做）还是微信内授权（只能走导出）
3. 可读页清单、完播率是否随统计周期变
4. 账号主体是否与抖音同一人/已认证
