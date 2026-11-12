"""SQLite storage: unique texts (+ FTS5 trigram + sqlite-vec vectors) and the chunks pointing to them."""
import hashlib
import sqlite3

import numpy as np
import sqlite_vec

from .config import DB_PATH, EMBED_DIM

SCHEMA = f"""
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS sources (
    path TEXT PRIMARY KEY, kind TEXT NOT NULL, mtime REAL NOT NULL, size INTEGER NOT NULL,
    indexed_at TEXT NOT NULL DEFAULT (datetime('now','localtime')), error TEXT
);
CREATE TABLE IF NOT EXISTS texts (
    id INTEGER PRIMARY KEY, hash TEXT UNIQUE NOT NULL, text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY, text_id INTEGER NOT NULL, path TEXT NOT NULL, kind TEXT NOT NULL,
    agent TEXT, title TEXT, project TEXT, ts TEXT, loc TEXT
);
CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
CREATE INDEX IF NOT EXISTS chunks_text ON chunks(text_id);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, tokenize='trigram');
CREATE VIRTUAL TABLE IF NOT EXISTS vec USING vec0(embedding float[{EMBED_DIM}] distance_metric=cosine);
"""


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, timeout=60, check_same_thread=False)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.executescript(SCHEMA)
    return db


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()


def known_sources(db) -> dict[str, tuple[float, int]]:
    return {p: (m, s) for p, m, s in db.execute("SELECT path, mtime, size FROM sources")}


def existing_text_ids(db, hashes: list[str]) -> dict[str, int]:
    out = {}
    for i in range(0, len(hashes), 500):
        part = hashes[i:i + 500]
        q = f"SELECT hash, id FROM texts WHERE hash IN ({','.join('?' * len(part))})"
        out.update(dict(db.execute(q, part)))
    return out


def add_text(db, h: str, text: str, vector: np.ndarray) -> int:
    cur = db.execute("INSERT INTO texts(hash, text) VALUES (?, ?)", (h, text))
    tid = cur.lastrowid
    db.execute("INSERT INTO fts(rowid, text) VALUES (?, ?)", (tid, text))
    db.execute("INSERT INTO vec(rowid, embedding) VALUES (?, ?)", (tid, vector.astype(np.float32).tobytes()))
    return tid


def remove_source(db, path: str):
    db.execute("DELETE FROM chunks WHERE path = ?", (path,))
    db.execute("DELETE FROM sources WHERE path = ?", (path,))


def prune_orphans(db) -> int:
    ids = [r[0] for r in db.execute(
        "SELECT id FROM texts WHERE NOT EXISTS (SELECT 1 FROM chunks c WHERE c.text_id = texts.id)")]
    for tid in ids:
        db.execute("DELETE FROM fts WHERE rowid = ?", (tid,))
        db.execute("DELETE FROM vec WHERE rowid = ?", (tid,))
        db.execute("DELETE FROM texts WHERE id = ?", (tid,))
    return len(ids)
