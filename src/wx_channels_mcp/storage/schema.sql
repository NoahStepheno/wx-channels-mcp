-- 视频号数据接入 v1 schema
-- 对齐 acceptance-v4 与 docs/spec-video-data-access.md §5

CREATE TABLE IF NOT EXISTS videos (
    video_id        TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL,
    title           TEXT,
    published_at    TEXT,             -- ISO8601
    time_precision  TEXT NOT NULL DEFAULT 'minute',  -- minute | date
    published_tz    TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    published_raw   TEXT,
    duration_sec    INTEGER,
    url             TEXT,
    visibility      TEXT NOT NULL DEFAULT 'unknown',  -- public | private | deleted | unknown
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    video_id        TEXT NOT NULL,
    snapshot_kind   TEXT NOT NULL,     -- d3 | d7 | backfill
    fetched_at      TEXT NOT NULL,     -- 首次冻结时间（UTC）
    last_seen_at    TEXT NOT NULL,
    offset_days     REAL NOT NULL,
    on_time         INTEGER NOT NULL DEFAULT 0,
    snapshot_late   INTEGER NOT NULL DEFAULT 0,
    play_count      INTEGER,
    finish_rate     REAL,
    avg_watch_sec   REAL,
    like_count      INTEGER,
    comment_count   INTEGER,
    share_count     INTEGER,
    favorite_count  INTEGER,
    follower_delta  INTEGER,
    period          TEXT,
    rate_unit       TEXT NOT NULL DEFAULT 'ratio',
    completeness    TEXT NOT NULL DEFAULT 'partial',  -- full | partial
    field_missing   TEXT NOT NULL DEFAULT '[]',
    enriched_fields TEXT NOT NULL DEFAULT '{}',
    anchor_status        TEXT NOT NULL DEFAULT 'unverified',
    anchor_unverified    INTEGER NOT NULL DEFAULT 1,
    replay_suspected     INTEGER NOT NULL DEFAULT 0,
    unit_mismatch_fields TEXT NOT NULL DEFAULT '[]',
    anchor_checked_at    TEXT,
    anchor_evidence      TEXT NOT NULL DEFAULT '{}',
    parser_version  TEXT NOT NULL,
    PRIMARY KEY (video_id, snapshot_kind)
);

CREATE TABLE IF NOT EXISTS field_absence (
    video_id            TEXT NOT NULL,
    field               TEXT NOT NULL,
    consecutive_missing INTEGER NOT NULL DEFAULT 0,
    first_missing_at    TEXT,
    last_checked_at     TEXT,
    batches_seen        TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'counting',  -- counting | unsupported
    PRIMARY KEY (video_id, field)
);

CREATE TABLE IF NOT EXISTS sync_batches (
    batch_id    TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,        -- ok | rate_limited | login_required | parse_failed
    reason      TEXT
);
