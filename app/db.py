"""
app/db.py — Base SQLite (un seul fichier, sauvegardable tel quel).

Tables :
  users      équipe de campagne (admin / éditeur / validateur)
  posts      un contenu source (photo(s), vidéo, texte → carrousel)
  assets     fichiers sources et médias générés par réseau
  variants   une déclinaison par réseau : légende, hashtags, tags, créneau…
  metrics    performances (API ou import CSV)
  settings   réglages éditoriaux (charte, contexte, comptes…)
  activity   journal d'équipe
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'editeur',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,                 -- photo | video | texte
  pillar TEXT DEFAULT '',             -- thème de campagne
  source_text TEXT DEFAULT '',
  brief TEXT DEFAULT '',
  networks TEXT NOT NULL DEFAULT '[]',
  options TEXT NOT NULL DEFAULT '{}', -- montage, sous-titres, recadrage…
  transcript TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'brouillon',
  progress TEXT DEFAULT '',
  error TEXT DEFAULT '',
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  role TEXT NOT NULL,                 -- source | output
  network TEXT DEFAULT '',
  fmt TEXT DEFAULT '',
  path TEXT NOT NULL,
  mime TEXT DEFAULT '',
  position INTEGER DEFAULT 0,
  token TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS variants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  network TEXT NOT NULL,
  caption TEXT DEFAULT '',
  hashtags TEXT DEFAULT '',
  first_comment TEXT DEFAULT '',
  alt_text TEXT DEFAULT '',
  title TEXT DEFAULT '',
  collaborators TEXT DEFAULT '',
  user_tags TEXT DEFAULT '',
  scheduled_at TEXT,
  approved_by INTEGER,
  status TEXT NOT NULL DEFAULT 'a_relire', -- a_relire | valide | programme | publie | echec | a_poster
  external_id TEXT DEFAULT '',
  external_url TEXT DEFAULT '',
  published_at TEXT,
  error TEXT DEFAULT '',
  UNIQUE(post_id, network)
);

CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  variant_id INTEGER REFERENCES variants(id) ON DELETE SET NULL,
  network TEXT NOT NULL,
  post_url TEXT DEFAULT '',
  external_id TEXT DEFAULT '',
  published_at TEXT,
  impressions INTEGER DEFAULT 0,
  reach INTEGER DEFAULT 0,
  likes INTEGER DEFAULT 0,
  comments INTEGER DEFAULT 0,
  shares INTEGER DEFAULT 0,
  saves INTEGER DEFAULT 0,
  video_views INTEGER DEFAULT 0,
  clicks INTEGER DEFAULT 0,
  followers_gained INTEGER DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'csv',
  label TEXT DEFAULT '',
  collected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

_write_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(path) -> None:
    conn = connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


class Store:
    """Accès base utilisable hors requête (threads de traitement, planificateur)."""

    def __init__(self, path):
        self.path = path

    def _conn(self):
        return connect(self.path)

    def query(self, sql, params=(), one=False):
        conn = self._conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            return (rows[0] if rows else None) if one else rows
        finally:
            conn.close()

    def execute(self, sql, params=()) -> int:
        with _write_lock:
            conn = self._conn()
            try:
                cur = conn.execute(sql, params)
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    # ---- réglages ---------------------------------------------------- #
    def get_setting(self, key, default=None):
        row = self.query("SELECT value FROM settings WHERE key=?", (key,), one=True)
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return row["value"]

    def set_setting(self, key, value) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def claim(self, sql, params=()) -> bool:
        """UPDATE conditionnel : True si une ligne a changé (évite les doubles publications)."""
        with _write_lock:
            conn = self._conn()
            try:
                cur = conn.execute(sql, params)
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def log(self, message: str, user_id=None) -> None:
        self.execute(
            "INSERT INTO activity(user_id, message, created_at) VALUES(?,?,?)",
            (user_id, message, now_iso()),
        )


def store() -> Store:
    return current_app.extensions["store"]
