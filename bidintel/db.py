import json
import sqlite3

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    name TEXT, dept TEXT, dept_code TEXT, format TEXT, version TEXT, status TEXT,
    published TEXT, end_date TEXT, description TEXT,
    contact_name TEXT, contact_email TEXT, contact_phone TEXT,
    detail_url TEXT, raw TEXT, first_seen TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS unspsc (
    event_id TEXT, code TEXT, description TEXT, segment TEXT,
    PRIMARY KEY (event_id, code)
);
CREATE TABLE IF NOT EXISTS attachments (
    event_id TEXT, file_name TEXT, local_path TEXT, ext TEXT, size_bytes INTEGER,
    download_url TEXT, pages INTEGER, chars INTEGER, text TEXT, error TEXT,
    PRIMARY KEY (event_id, file_name)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    event_id UNINDEXED, source UNINDEXED, body, tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS facts (
    event_id TEXT PRIMARY KEY, json TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS analysis (
    event_id TEXT PRIMARY KEY, model TEXT, input_hash TEXT, json TEXT,
    seconds REAL, created TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS briefs (
    kind TEXT PRIMARY KEY, model TEXT, body TEXT, created TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    business_name TEXT,
    role TEXT NOT NULL CHECK(role IN ('superadmin', 'admin', 'employee')),
    is_active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    action TEXT NOT NULL,
    details TEXT,
    ip_address TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    # v2: multi-source bids. source = 'Cal eProcure' | 'OpenGov'; level = 'State' | 'Local';
    # extra = source-specific fields (solicitation #, NIGP codes, Q&A deadline, procurement contact, ...)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    for col in ("source", "level", "extra"):
        if col not in cols:
            conn.execute(f"ALTER TABLE events ADD COLUMN {col} TEXT")
    if conn.execute("SELECT 1 FROM events WHERE source IS NULL LIMIT 1").fetchone():  # write only when needed
        conn.execute("UPDATE events SET source='Cal eProcure', level='State' WHERE source IS NULL")
        conn.commit()
    return conn


def loads(value, default=None):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
