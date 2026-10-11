import sqlite3
from pathlib import Path

# Constant DDL — the only statements run through executescript (never built from input).
SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    state_json  TEXT NOT NULL,
    duration_ms INTEGER,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_by_status ON runs (status);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    type        TEXT NOT NULL,
    data_json   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS events_by_run ON events (run_id, id);
CREATE TABLE IF NOT EXISTS memories (
    run_id      TEXT PRIMARY KEY,
    question    TEXT NOT NULL,
    summary     TEXT NOT NULL,
    source_titles_json TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_cache (
    key           TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wiki_cache (
    key           TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    fetched_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    name        TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_cache (
    key           TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    fetched_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS page_cache (
    url_hash      TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    text          TEXT NOT NULL,
    media_type    TEXT NOT NULL,
    fetched_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS embedding_cache (
    model         TEXT NOT NULL,
    key           TEXT NOT NULL,
    vector        BLOB NOT NULL,
    PRIMARY KEY (model, key)
);
CREATE TABLE IF NOT EXISTS workspaces (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL,
    name          TEXT NOT NULL,
    media_type    TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    sha256        TEXT NOT NULL,
    status        TEXT NOT NULL,
    pages         INTEGER,
    chunks        INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    created_at    TEXT NOT NULL,
    expires_at    TEXT
);
CREATE INDEX IF NOT EXISTS documents_by_ws ON documents (workspace_id);
CREATE UNIQUE INDEX IF NOT EXISTS documents_ws_hash ON documents (workspace_id, sha256);
CREATE TABLE IF NOT EXISTS outbox (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    kind            TEXT NOT NULL,
    event           TEXT NOT NULL,
    target          TEXT NOT NULL,
    secret          TEXT,  -- v1.1: always NULL (callback secrets are derived)
    payload_json    TEXT NOT NULL,
    status          TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_status     INTEGER,
    last_error      TEXT,
    created_at      TEXT NOT NULL,
    delivered_at    TEXT
);
CREATE INDEX IF NOT EXISTS outbox_due ON outbox (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS outbox_by_run ON outbox (run_id);
CREATE TABLE IF NOT EXISTS telegram_updates (
    update_id     INTEGER PRIMARY KEY,
    received_at   TEXT NOT NULL
);
-- v1.2: the current conversation of each Telegram chat (/new resets it)
CREATE TABLE IF NOT EXISTS chat_conversations (
    chat_id         TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# v1.2: runs gain a conversation id. Constant DDL; the backfill uses the same rule as
# run.legacy_conversation_id (each older run is its own conversation).
_RUN_COLUMNS = "PRAGMA table_info(runs)"
_ADD_CONVERSATION = "ALTER TABLE runs ADD COLUMN conversation_id TEXT"
_BACKFILL_CONVERSATION = (
    "UPDATE runs SET conversation_id = 'conv_' || substr(substr(id, 5) || '0000', 1, 16) "
    "WHERE conversation_id IS NULL"
)
_CONVERSATION_INDEX = (
    "CREATE INDEX IF NOT EXISTS runs_by_conversation ON runs (conversation_id, created_at)"
)


def init(path: str) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        if "conversation_id" not in {r["name"] for r in conn.execute(_RUN_COLUMNS)}:
            conn.execute(_ADD_CONVERSATION)
        conn.execute(_BACKFILL_CONVERSATION)
        conn.execute(_CONVERSATION_INDEX)
