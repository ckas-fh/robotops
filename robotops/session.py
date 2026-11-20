"""Per-visitor database isolation.

In ``demo`` mode (default) every browser session gets its own SQLite file,
copied from a freshly seeded template, so one visitor's edits are never seen
by another. Stale session files are purged automatically. In ``shared`` mode
(``ROBOTOPS_MODE=shared``) a single persistent database is used, for local or
single-team deployments.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from . import db, seed

SESSION_TTL_SECONDS = int(os.environ.get("ROBOTOPS_SESSION_TTL", 6 * 3600))
MAX_SESSIONS = int(os.environ.get("ROBOTOPS_MAX_SESSIONS", 300))
_SID_RE = re.compile(r"^[a-f0-9]{32}$")


def mode() -> str:
    return "shared" if os.environ.get("ROBOTOPS_MODE", "demo").lower() == "shared" else "demo"


def data_dir() -> Path:
    d = Path(os.environ.get("ROBOTOPS_DATA_DIR", Path(tempfile.gettempdir()) / "robotops"))
    (d / "sessions").mkdir(parents=True, exist_ok=True)
    return d


def seed_database(path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    conn = db.connect(tmp)
    try:
        seed.generate(conn)
    finally:
        conn.close()
    os.replace(tmp, path)  # atomic: readers never see a half-written DB


def purge_stale(now: float | None = None) -> int:
    """Delete expired session DBs and enforce MAX_SESSIONS. Returns count removed."""
    now = now or time.time()
    files = sorted((data_dir() / "sessions").glob("*.db"), key=lambda p: p.stat().st_mtime)
    removed = 0
    for i, f in enumerate(files):
        if now - f.stat().st_mtime > SESSION_TTL_SECONDS or len(files) - i > MAX_SESSIONS:
            f.unlink(missing_ok=True)
            removed += 1
    return removed


def db_path(session_id: str) -> Path:
    """Return the DB for this visitor, creating and seeding it on first use."""
    if mode() == "shared":
        path = data_dir() / "robotops.db"
    else:
        if not _SID_RE.match(session_id):
            raise ValueError("Invalid session id")
        path = data_dir() / "sessions" / f"{session_id}.db"
    if not path.exists():
        if mode() == "demo":
            purge_stale()
        seed_database(path)
    elif _outdated(path):
        seed_database(path)  # created by an older app version; rebuild with the current schema
    else:
        path.touch()  # keep active sessions alive
    return path


def _outdated(path: Path) -> bool:
    conn = db.connect(path)
    try:
        return db.schema_version(conn) != db.SCHEMA_VERSION
    finally:
        conn.close()


def reset(session_id: str) -> Path:
    path = db_path(session_id)
    seed_database(path)
    return path


def discard(session_id: str) -> None:
    if mode() == "demo" and _SID_RE.match(session_id):
        (data_dir() / "sessions" / f"{session_id}.db").unlink(missing_ok=True)
