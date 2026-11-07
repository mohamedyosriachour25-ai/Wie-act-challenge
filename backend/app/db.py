"""Tiny SQLite layer. Only pseudonymous data is stored: no names, e-mails, photos or audio."""
from __future__ import annotations

import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pseudo TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    learning_style TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    topic TEXT NOT NULL,
    correct INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, ts);
CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection TEXT NOT NULL,
    title TEXT NOT NULL,
    owner_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    chunks_total INTEGER NOT NULL DEFAULT 0,
    chunks_done INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_docs_collection ON docs(collection);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    emb BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS blobs (
    doc_id INTEGER PRIMARY KEY,
    mime TEXT NOT NULL,
    data BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS rooms (
    code TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    capacity INTEGER NOT NULL,
    owner_id INTEGER,
    is_public INTEGER NOT NULL DEFAULT 1,
    focus_min INTEGER NOT NULL DEFAULT 25,
    break_min INTEGER NOT NULL DEFAULT 5,
    created_at REAL NOT NULL
);
"""

LIVE_ROOMS = [
    ("LIVE-DEEP", "Deep work", "Quiet focus, no chat. Reactions only.", 25, 5),
    ("LIVE-LONG", "Long sessions", "Longer blocks for heavy reading and problem sets.", 50, 10),
    ("LIVE-SPRINT", "Sprints", "Short bursts when motivation is low.", 15, 3),
]


class DB:
    def __init__(self, path: str):
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.execute("PRAGMA busy_timeout=5000")
            if path != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")  # readers do not block the writer; safer on crash
                self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(SCHEMA)
            cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(docs)")}
            if "kind" not in cols:  # older databases: images need a kind to tell them from text
                self.conn.execute("ALTER TABLE docs ADD COLUMN kind TEXT NOT NULL DEFAULT 'text'")
            self.conn.commit()

    def execute(self, sql: str, params: tuple = ()):
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        with self._lock:
            self.conn.executemany(sql, rows)
            self.conn.commit()

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def seed_live_rooms(self, capacity: int) -> None:
        base = time.time()
        for i, (code, name, topic, focus, brk) in enumerate(LIVE_ROOMS):
            self.execute(
                "INSERT OR IGNORE INTO rooms (code, kind, name, topic, capacity, is_public, focus_min, break_min, created_at)"
                " VALUES (?, 'live', ?, ?, ?, 1, ?, ?, ?)",
                (code, name, topic, capacity, focus, brk, base + i * 0.001),
            )
