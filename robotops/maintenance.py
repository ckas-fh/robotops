"""Robot inspections and maintenance tickets.

Business rules
--------------
* Logging an inspection updates the robot's ``last_inspection`` date.
* A **Fail** result opens a High-priority ticket and moves the robot to
  ``Maintenance``. **Needs attention** opens a Medium ticket only.
* Opening a **Critical** ticket marks the robot ``Down``.
* Closing a ticket returns the robot to ``Operational`` once it has no
  remaining High/Critical open tickets.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import clock
from .constants import (
    INSPECTION_KINDS,
    INSPECTION_RESULTS,
    OPEN_TICKET_STATUSES,
    ROBOT_STATUSES,
    TICKET_PRIORITIES,
    TICKET_STATUSES,
)

DUE_SOON_DAYS = 3


class MaintenanceError(ValueError):
    pass


def inspection_state(last_inspection: str | None, interval_days: int, today: date) -> tuple[str, date | None, int | None]:
    """Return (state, next_due, days_until_due). State: Overdue / Due soon / OK / Never inspected."""
    if not last_inspection:
        return "Never inspected", None, None
    due = clock.parse_date(last_inspection) + timedelta(days=interval_days)
    days = (due - today).days
    if days < 0:
        return "Overdue", due, days
    if days <= DUE_SOON_DAYS:
        return "Due soon", due, days
    return "OK", due, days


def _robot(conn: sqlite3.Connection, robot_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM robots WHERE id = ?", (robot_id,)).fetchone()
    if row is None:
        raise MaintenanceError(f"Unknown robot id {robot_id}")
    return row


def set_robot_status(conn: sqlite3.Connection, robot_id: int, status: str) -> None:
    if status not in ROBOT_STATUSES:
        raise MaintenanceError(f"Invalid status {status!r}")
    _robot(conn, robot_id)
    conn.execute("UPDATE robots SET status = ? WHERE id = ?", (status, robot_id))


@dataclass
class InspectionResult:
    inspection_id: int
    ticket_id: int | None = None
    messages: list[str] = field(default_factory=list)


def log_inspection(
    conn: sqlite3.Connection,
    robot_id: int,
    inspector: str,
    kind: str,
    result: str,
    notes: str = "",
    performed_on: date | None = None,
) -> InspectionResult:
    if kind not in INSPECTION_KINDS:
        raise MaintenanceError(f"Invalid inspection type {kind!r}")
    if result not in INSPECTION_RESULTS:
        raise MaintenanceError(f"Invalid result {result!r}")
    if not inspector.strip():
        raise MaintenanceError("Inspector is required.")
    performed_on = performed_on or clock.now().date()
    if performed_on > clock.now().date():
        raise MaintenanceError("Inspection date cannot be in the future.")
    robot = _robot(conn, robot_id)

    cur = conn.execute(
        "INSERT INTO inspections (robot_id, performed_on, inspector, kind, result, notes) VALUES (?, ?, ?, ?, ?, ?)",
        (robot_id, clock.fmt_date(performed_on), inspector.strip(), kind, result, notes.strip()),
    )
    out = InspectionResult(inspection_id=cur.lastrowid)

    # Only move last_inspection forward (back-dated entries don't reset the clock).
    if not robot["last_inspection"] or clock.parse_date(robot["last_inspection"]) <= performed_on:
        conn.execute("UPDATE robots SET last_inspection = ? WHERE id = ?", (clock.fmt_date(performed_on), robot_id))

    if result == "Fail":
        out.ticket_id = create_ticket(
            conn,
            robot_id,
            title=f"Failed {kind.lower()} inspection",
            description=notes.strip() or "Auto-created from failed inspection.",
            priority="High",
            assignee=inspector.strip(),
        )
        if robot["status"] != "Down":
            set_robot_status(conn, robot_id, "Maintenance")
        out.messages.append(f"{robot['name']} moved to Maintenance and ticket #{out.ticket_id} opened.")
    elif result == "Needs attention":
        out.ticket_id = create_ticket(
            conn,
            robot_id,
            title=f"Follow-up from {kind.lower()} inspection",
            description=notes.strip() or "Auto-created: inspection flagged items needing attention.",
            priority="Medium",
            assignee=inspector.strip(),
        )
        out.messages.append(f"Follow-up ticket #{out.ticket_id} opened for {robot['name']}.")
    return out


def create_ticket(
    conn: sqlite3.Connection,
    robot_id: int,
    title: str,
    priority: str,
    assignee: str,
    description: str = "",
    opened_on: datetime | None = None,
) -> int:
    if priority not in TICKET_PRIORITIES:
        raise MaintenanceError(f"Invalid priority {priority!r}")
    if not title.strip():
        raise MaintenanceError("Ticket title is required.")
    _robot(conn, robot_id)
    cur = conn.execute(
        """INSERT INTO tickets (robot_id, title, description, priority, status, assignee, opened_on)
           VALUES (?, ?, ?, ?, 'New', ?, ?)""",
        (robot_id, title.strip(), description.strip(), priority, assignee.strip() or "Unassigned", clock.fmt_ts(opened_on or clock.now())),
    )
    if priority == "Critical":
        set_robot_status(conn, robot_id, "Down")
    return cur.lastrowid


def update_ticket(conn: sqlite3.Connection, ticket_id: int, status: str, assignee: str | None = None) -> None:
    if status not in TICKET_STATUSES:
        raise MaintenanceError(f"Invalid status {status!r}")
    if status == "Closed":
        raise MaintenanceError("Use close_ticket() to close a ticket.")
    ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if ticket is None:
        raise MaintenanceError(f"Unknown ticket #{ticket_id}")
    if ticket["status"] == "Closed":
        raise MaintenanceError(f"Ticket #{ticket_id} is already closed.")
    conn.execute(
        "UPDATE tickets SET status = ?, assignee = COALESCE(?, assignee) WHERE id = ?",
        (status, assignee.strip() if assignee else None, ticket_id),
    )


def close_ticket(
    conn: sqlite3.Connection,
    ticket_id: int,
    resolution: str,
    closed_on: datetime | None = None,
) -> list[str]:
    """Close a ticket and restore robot availability. Returns user-facing messages."""
    ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if ticket is None:
        raise MaintenanceError(f"Unknown ticket #{ticket_id}")
    if ticket["status"] == "Closed":
        raise MaintenanceError(f"Ticket #{ticket_id} is already closed.")
    if not resolution.strip():
        raise MaintenanceError("A resolution note is required to close a ticket.")
    closed_on = closed_on or clock.now()
    messages: list[str] = []

    conn.execute(
        "UPDATE tickets SET status = 'Closed', closed_on = ?, resolution = ? WHERE id = ?",
        (clock.fmt_ts(closed_on), resolution.strip(), ticket_id),
    )

    robot = _robot(conn, ticket["robot_id"])
    blocking = conn.execute(
        f"""SELECT COUNT(*) FROM tickets WHERE robot_id = ? AND priority IN ('High', 'Critical')
            AND status IN ({','.join('?' * len(OPEN_TICKET_STATUSES))})""",
        (ticket["robot_id"], *OPEN_TICKET_STATUSES),
    ).fetchone()[0]
    if robot["status"] in ("Maintenance", "Down") and blocking == 0:
        set_robot_status(conn, robot["id"], "Operational")
        messages.append(f"{robot['name']} returned to Operational.")
    return messages


def link_github_issue(conn: sqlite3.Connection, ticket_id: int, url: str) -> None:
    """Record the GitHub issue created for a ticket. Each ticket can be linked once."""
    ticket = conn.execute("SELECT github_issue_url FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if ticket is None:
        raise MaintenanceError(f"Unknown ticket #{ticket_id}")
    if ticket["github_issue_url"]:
        raise MaintenanceError(f"Ticket #{ticket_id} is already linked to {ticket['github_issue_url']}.")
    conn.execute("UPDATE tickets SET github_issue_url = ? WHERE id = ?", (url, ticket_id))
