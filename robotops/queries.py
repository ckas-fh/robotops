"""Read-side queries returning pandas DataFrames for the UI."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

import pandas as pd

from . import clock, maintenance
from .constants import AVAILABLE_STATUSES, OPEN_TICKET_STATUSES

_OPEN = ",".join(f"'{s}'" for s in OPEN_TICKET_STATUSES)


def robots(conn: sqlite3.Connection, today: date | None = None) -> pd.DataFrame:
    today = today or clock.now().date()
    df = pd.read_sql_query(
        f"""SELECT r.*,
                   (SELECT COUNT(*) FROM tickets t WHERE t.robot_id = r.id AND t.status IN ({_OPEN})) AS open_tickets,
                   (SELECT i.result FROM inspections i WHERE i.robot_id = r.id
                    ORDER BY i.performed_on DESC, i.id DESC LIMIT 1) AS last_result
            FROM robots r ORDER BY r.name""",
        conn,
    )
    states = [maintenance.inspection_state(li, iv, today) for li, iv in zip(df["last_inspection"], df["inspection_interval"])]
    df["inspection_state"] = [s[0] for s in states]
    df["next_inspection"] = [s[1] for s in states]
    df["days_to_inspection"] = [s[2] for s in states]
    return df


def inspections(conn: sqlite3.Connection, robot_id: int | None = None, limit: int = 500) -> pd.DataFrame:
    where, params = ("WHERE i.robot_id = ?", (robot_id,)) if robot_id else ("", ())
    return pd.read_sql_query(
        f"""SELECT i.id, i.performed_on, r.name AS robot, i.kind, i.result, i.inspector, i.notes
            FROM inspections i JOIN robots r ON r.id = i.robot_id {where}
            ORDER BY i.performed_on DESC, i.id DESC LIMIT ?""",
        conn,
        params=(*params, limit),
    )


def tickets(conn: sqlite3.Connection, include_closed: bool = True, robot_id: int | None = None) -> pd.DataFrame:
    clauses, params = [], []
    if not include_closed:
        clauses.append(f"t.status IN ({_OPEN})")
    if robot_id:
        clauses.append("t.robot_id = ?")
        params.append(robot_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return pd.read_sql_query(
        f"""SELECT t.id, r.name AS robot, r.site, t.robot_id, t.title, t.priority, t.status, t.assignee,
                   t.opened_on, t.closed_on, t.description, t.resolution, t.github_issue_url
            FROM tickets t JOIN robots r ON r.id = t.robot_id {where}
            ORDER BY CASE t.status WHEN 'Closed' THEN 1 ELSE 0 END,
                     CASE t.priority WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,
                     t.opened_on""",
        conn,
        params=params,
    )


def fleet_kpis(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    now = now or clock.now()
    r = robots(conn, now.date())
    total = len(r)
    available = int(r["status"].isin(AVAILABLE_STATUSES).sum())
    return {
        "fleet_size": total,
        "available": available,
        "availability_pct": round(100 * available / total, 1) if total else 0.0,
        "down": int((r["status"] == "Down").sum()),
        "in_maintenance": int((r["status"] == "Maintenance").sum()),
        "avg_battery": round(float(r["battery_pct"].mean()), 1) if total else 0.0,
        "avg_health": round(float(r["health_score"].mean()), 1) if total else 0.0,
        "overdue_inspections": int((r["inspection_state"] == "Overdue").sum()),
        "due_soon_inspections": int((r["inspection_state"] == "Due soon").sum()),
        "open_tickets": int(r["open_tickets"].sum()),
    }


def teleop_sessions(
    conn: sqlite3.Connection,
    start: datetime | None = None,
    end: datetime | None = None,
    status: str | None = None,
    robot: str | None = None,
) -> pd.DataFrame:
    clauses, params = [], []
    if start:
        clauses.append("s.end_at > ?")
        params.append(clock.fmt_ts(start))
    if end:
        clauses.append("s.start_at < ?")
        params.append(clock.fmt_ts(end))
    if status:
        clauses.append("s.status = ?")
        params.append(status)
    if robot:
        clauses.append("r.name = ?")
        params.append(robot)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return pd.read_sql_query(
        f"""SELECT s.id, r.name AS robot, r.model, s.operator, s.task, s.start_at, s.end_at,
                   s.target_episodes, s.collected_episodes, s.status
            FROM teleop_sessions s JOIN robots r ON r.id = s.robot_id {where}
            ORDER BY s.start_at, s.id""",
        conn,
        params=params,
    )
