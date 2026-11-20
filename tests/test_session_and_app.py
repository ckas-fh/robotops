import os
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from robotops import db, session

APP = str(Path(__file__).resolve().parents[1] / "app.py")
A, B = "a" * 32, "b" * 32


def _count(path, sql):
    c = db.connect(path)
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


def test_sessions_are_isolated():
    pa, pb = session.db_path(A), session.db_path(B)
    assert pa != pb
    c = db.connect(pa)
    c.execute("DELETE FROM robots")
    c.commit()
    c.close()
    assert _count(pa, "SELECT COUNT(*) FROM robots") == 0
    assert _count(pb, "SELECT COUNT(*) FROM robots") == 20


def test_reset_restores_seed_data():
    p = session.db_path(A)
    c = db.connect(p)
    c.execute("DELETE FROM tickets")
    c.commit()
    c.close()
    session.reset(A)
    assert _count(p, "SELECT COUNT(*) FROM tickets") > 0


def test_invalid_session_id_rejected():
    with pytest.raises(ValueError):
        session.db_path("../../etc/passwd")


def test_stale_sessions_purged():
    p = session.db_path(A)
    old = time.time() - session.SESSION_TTL_SECONDS - 10
    os.utime(p, (old, old))
    assert session.purge_stale() == 1 and not p.exists()


def test_shared_mode_uses_single_db(monkeypatch):
    monkeypatch.setenv("ROBOTOPS_MODE", "shared")
    assert session.db_path(A) == session.db_path(B)


# --- Streamlit UI smoke tests -------------------------------------------
ROOT = str(Path(APP).parent)
PAGES = ["dashboard", "maintenance_page"]


def test_app_shell_renders():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception, at.exception
    assert any("Demo sandbox" in i.value for i in at.info)


@pytest.mark.parametrize("page", PAGES)
def test_each_page_renders_without_errors(page):
    script = f"import sys; sys.path.insert(0, {ROOT!r})\nfrom views import {page}\n{page}.render()"
    at = AppTest.from_string(script, default_timeout=30).run()
    assert not at.exception, at.exception
    assert at.markdown  # header rendered


def test_logging_failed_inspection_through_ui():
    script = f"import sys; sys.path.insert(0, {ROOT!r})\nfrom views import maintenance_page\nmaintenance_page.render()"
    at = AppTest.from_string(script, default_timeout=30).run()
    at.radio[0].set_value("Fail")
    at.button(key="FormSubmitter:inspection-Log inspection").click().run()
    assert not at.exception, at.exception
    path = session.db_path(at.session_state.sid)
    assert _count(path, "SELECT COUNT(*) FROM tickets WHERE title LIKE 'Failed%'") == 1


def test_two_app_visitors_get_different_databases():
    a, b = AppTest.from_file(APP, default_timeout=30), AppTest.from_file(APP, default_timeout=30)
    a.run()
    b.run()
    assert a.session_state.sid != b.session_state.sid


def test_github_dry_run_requires_approval_in_ui():
    script = f"import sys; sys.path.insert(0, {ROOT!r})\nfrom views import maintenance_page\nmaintenance_page.render()"
    at = AppTest.from_string(script, default_timeout=30).run()
    button = next(b for b in at.button if b.label == "Run dry run")
    assert button.disabled
    next(c for c in at.checkbox if c.key.startswith("gh_approve_")).check().run()
    next(b for b in at.button if b.label == "Run dry run").click().run()
    assert not at.exception
    assert any("Dry run complete" in s.value for s in at.success)
