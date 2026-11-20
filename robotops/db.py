"""SQLite schema and connection helpers."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# Bump when the schema changes; session.py re-seeds databases with an older version.
SCHEMA_VERSION = "2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS robots (
    id                  INTEGER PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    model               TEXT NOT NULL,
    robot_class         TEXT NOT NULL,
    site                TEXT NOT NULL,
    status              TEXT NOT NULL,
    battery_pct         INTEGER NOT NULL,
    health_score        INTEGER NOT NULL,
    uptime_hours        REAL NOT NULL,
    firmware            TEXT NOT NULL,
    commissioned_on     TEXT NOT NULL,
    last_inspection     TEXT,
    inspection_interval INTEGER NOT NULL DEFAULT 30
);

CREATE TABLE IF NOT EXISTS inspections (
    id          INTEGER PRIMARY KEY,
    robot_id    INTEGER NOT NULL REFERENCES robots(id) ON DELETE CASCADE,
    performed_on TEXT NOT NULL,
    inspector   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    result      TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY,
    robot_id    INTEGER NOT NULL REFERENCES robots(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    priority    TEXT NOT NULL,
    status      TEXT NOT NULL,
    assignee    TEXT NOT NULL,
    opened_on   TEXT NOT NULL,
    closed_on   TEXT,
    resolution  TEXT NOT NULL DEFAULT '',
    github_issue_url TEXT
);

CREATE TABLE IF NOT EXISTS teleop_sessions (
    id                 INTEGER PRIMARY KEY,
    robot_id           INTEGER NOT NULL REFERENCES robots(id) ON DELETE CASCADE,
    operator           TEXT NOT NULL,
    task               TEXT NOT NULL,
    start_at           TEXT NOT NULL,
    end_at             TEXT NOT NULL,
    target_episodes    INTEGER NOT NULL,
    collected_episodes INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_inspections_robot ON inspections(robot_id);
CREATE INDEX IF NOT EXISTS idx_tickets_robot ON tickets(robot_id);
CREATE INDEX IF NOT EXISTS idx_teleop_start ON teleop_sessions(start_at);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10, detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on error, always close."""
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def schema_version(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def clear_all(conn: sqlite3.Connection) -> None:
    for table in ("teleop_sessions", "tickets", "inspections", "robots", "meta"):
        conn.execute(f"DELETE FROM {table}")
