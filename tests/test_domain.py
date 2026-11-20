import pytest

from robotops import db, maintenance, queries, seed
from tests.conftest import NOW, robot_id


def _status(conn, rid):
    return conn.execute("SELECT status FROM robots WHERE id = ?", (rid,)).fetchone()[0]


def test_seed_is_deterministic_and_realistic(conn, tmp_path):
    c2 = db.connect(tmp_path / "t2.db")
    seed.generate(c2, now=NOW)
    dump = lambda c: [tuple(r) for r in c.execute("SELECT name, site, status, battery_pct FROM robots ORDER BY id")]
    assert dump(conn) == dump(c2)
    c2.close()
    k = queries.fleet_kpis(conn, NOW)
    assert k["fleet_size"] == 20 and k["down"] >= 1 and k["in_maintenance"] >= 1 and k["open_tickets"] >= 1
    assert {r[1] for r in dump(conn)} == {"San Francisco Lab"}
    assert {r[0] for r in conn.execute("SELECT DISTINCT model FROM robots")} == {"Kestrel", "Heron"}


def test_failed_inspection_opens_ticket_and_sets_maintenance(conn):
    rid = robot_id(conn)
    res = maintenance.log_inspection(conn, rid, "Ana", "Routine", "Fail", "bad", NOW.date())
    assert res.ticket_id and _status(conn, rid) == "Maintenance"
    assert conn.execute("SELECT last_inspection FROM robots WHERE id = ?", (rid,)).fetchone()[0] == NOW.date().isoformat()


def test_needs_attention_opens_medium_ticket_only(conn):
    rid = robot_id(conn)
    res = maintenance.log_inspection(conn, rid, "Ana", "Routine", "Needs attention", "", NOW.date())
    assert conn.execute("SELECT priority FROM tickets WHERE id = ?", (res.ticket_id,)).fetchone()[0] == "Medium"
    assert _status(conn, rid) == "Operational"


def test_backdated_inspection_does_not_reset_clock(conn):
    rid = robot_id(conn)
    before = conn.execute("SELECT last_inspection FROM robots WHERE id = ?", (rid,)).fetchone()[0]
    maintenance.log_inspection(conn, rid, "Ana", "Routine", "Pass", "", NOW.date().replace(year=2025))
    assert conn.execute("SELECT last_inspection FROM robots WHERE id = ?", (rid,)).fetchone()[0] == before


def test_critical_ticket_downs_robot_and_closing_restores(conn):
    rid = robot_id(conn)
    tid = maintenance.create_ticket(conn, rid, "Broken", "Critical", "Ana")
    assert _status(conn, rid) == "Down"
    msgs = maintenance.close_ticket(conn, tid, "Fixed", closed_on=NOW)
    assert _status(conn, rid) == "Operational" and any("Operational" in m for m in msgs)
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.close_ticket(conn, tid, "again")


def test_robot_stays_down_while_other_blocking_ticket_open(conn):
    rid = robot_id(conn)
    t1 = maintenance.create_ticket(conn, rid, "A", "Critical", "Ana")
    maintenance.create_ticket(conn, rid, "B", "High", "Ana")
    maintenance.close_ticket(conn, t1, "Fixed A")
    assert _status(conn, rid) == "Down"


def test_validation(conn):
    rid = robot_id(conn)
    tid = maintenance.create_ticket(conn, rid, "x", "Low", "Ana")
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.close_ticket(conn, tid, "  ")
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.create_ticket(conn, rid, "", "Low", "Ana")
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.log_inspection(conn, rid, "", "Routine", "Pass")
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.update_ticket(conn, tid, "Closed")


def test_update_ticket(conn):
    tid = maintenance.create_ticket(conn, robot_id(conn), "x", "Low", "Ana")
    maintenance.update_ticket(conn, tid, "Waiting on parts", "Hana Sato")
    assert tuple(conn.execute("SELECT status, assignee FROM tickets WHERE id = ?", (tid,)).fetchone()) == ("Waiting on parts", "Hana Sato")


def test_inspection_state():
    d = NOW.date()
    assert maintenance.inspection_state("2026-03-01", 30, d)[0] == "OK"
    assert maintenance.inspection_state("2026-02-01", 30, d)[0] == "Overdue"
    assert maintenance.inspection_state("2026-02-08", 30, d)[0] == "Due soon"
    assert maintenance.inspection_state(None, 30, d)[0] == "Never inspected"
