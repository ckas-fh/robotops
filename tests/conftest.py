import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robotops import db, seed  # noqa: E402

NOW = datetime(2026, 3, 10, 12, 0)  # a Tuesday


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ROBOTOPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("ROBOTOPS_MODE", raising=False)
    # Never let a developer's real credentials or endpoints leak into tests.
    for var in ("GITHUB_TOKEN", "ROBOTOPS_GITHUB_REPO", "ROBOTOPS_GITHUB_DRY_RUN", "ROBOTOPS_API_KEY", "ROBOTOPS_API_URL"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    seed.generate(c, now=NOW)
    yield c
    c.close()


def robot_id(conn, status="Operational"):
    q = "SELECT id FROM robots WHERE status = ? ORDER BY id"
    return conn.execute(q, (status,)).fetchone()["id"]
