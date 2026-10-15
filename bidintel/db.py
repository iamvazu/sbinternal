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
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def loads(value, default=None):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
