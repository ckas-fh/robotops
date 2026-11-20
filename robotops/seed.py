"""Deterministic synthetic data generator (robots, inspections, tickets).

All data is fictional. Dates are generated relative to ``now`` so the demo
always looks current, and a fixed RNG seed keeps it reproducible.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta

from . import clock, db
from .constants import MODELS, OPERATORS, TECHNICIANS

DEFAULT_SEED = 7

FLEET = [
    # (prefix, model, site, count)
    ("KST", "Kestrel", "San Francisco Lab", 10),
    ("HRN", "Heron", "San Francisco Lab", 10),
]

# A fixed, plausible story: which robots are not operational and why.
INCIDENTS = {
    # robot index -> (status, ticket title, priority, ticket status, days open)
    2: ("Down", "Drive motor controller fault (E-217)", "Critical", "In progress", 1),
    7: ("Maintenance", "LiDAR returns degraded on rear sensor", "High", "New", 2),
    12: ("Maintenance", "Wrist camera intermittent dropout", "High", "Waiting on parts", 9),
    18: ("Down", "Elbow joint collision fault; inspect harmonic drive", "Critical", "New", 0),
    15: ("Maintenance", "Thermal throttling on compute module", "High", "In progress", 4),
}

_COMMON_ISSUES = [("Firmware update pending (v4.3.1)", "Low"), ("Intermittent Wi-Fi drop in aisle 7", "Low")]
MINOR_ISSUES = {
    "Kestrel": _COMMON_ISSUES + [
        ("Squeaking caster under load", "Low"),
        ("LiDAR window needs cleaning / recalibration", "Medium"),
        ("Charging contacts pitted", "Medium"),
        ("Bumper strip cracked", "Medium"),
    ],
    "Heron": _COMMON_ISSUES + [
        ("Fingertip pads showing wear", "Medium"),
        ("Cable chafing near shoulder joint", "Medium"),
        ("Wrist camera lens smudged", "Low"),
        ("Gripper force calibration drifting", "Medium"),
    ],
}

INSPECTION_NOTES = {
    "Pass": ["All checks nominal.", "Torque checks within spec.", "Sensors calibrated; no issues.", "Clean bill of health."],
    "Needs attention": ["Minor wear on end effector.", "Battery capacity at 84% of nominal.", "Loose cable tie on harness."],
    "Fail": ["E-stop response exceeded 250 ms.", "Joint backlash out of tolerance.", "LiDAR returns degraded."],
}

FIRMWARE = ["v4.2.0", "v4.2.3", "v4.3.0", "v4.3.1"]


def generate(conn: sqlite3.Connection, now: datetime | None = None, seed: int = DEFAULT_SEED) -> None:
    """Clear and repopulate all tables with synthetic data."""
    now = (now or clock.now()).replace(second=0, microsecond=0)
    today = now.date()
    rng = random.Random(seed)
    db.init_schema(conn)
    db.clear_all(conn)

    # --- Robots ----------------------------------------------------------
    robot_rows = []
    counters: dict[str, int] = {}
    idx = 0
    for prefix, model, site, count in FLEET:
        robot_class, interval = MODELS[model]
        for _ in range(count):
            counters[prefix] = counters.get(prefix, 0) + 1
            name = f"{prefix}-{counters[prefix]:03d}"
            status = INCIDENTS.get(idx, ("Operational",))[0]
            if status == "Operational" and rng.random() < 0.18:
                status = "Charging"
            battery = rng.randint(8, 35) if status == "Charging" else rng.randint(18, 100)
            if status == "Down":
                battery = rng.randint(5, 60)
            health = rng.randint(45, 70) if status in ("Down", "Maintenance") else rng.randint(68, 99)
            # Mostly current inspections, a few overdue / due soon.
            roll = rng.random()
            if roll < 0.15:
                days_ago = interval + rng.randint(1, 12)
            elif roll < 0.3:
                days_ago = interval - rng.randint(0, 3)
            else:
                days_ago = rng.randint(1, max(interval - 5, 2))
            commissioned = today - timedelta(days=rng.randint(120, 900))
            uptime = round((today - commissioned).days * rng.uniform(5.0, 14.0), 1)
            cur = conn.execute(
                """INSERT INTO robots (name, model, robot_class, site, status, battery_pct, health_score, uptime_hours,
                                       firmware, commissioned_on, last_inspection, inspection_interval)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, model, robot_class, site, status, battery, health, uptime, rng.choice(FIRMWARE),
                 clock.fmt_date(commissioned), clock.fmt_date(today - timedelta(days=days_ago)), interval),
            )
            robot_rows.append({"id": cur.lastrowid, "idx": idx, "name": name, "model": model, "status": status,
                               "interval": interval, "last": days_ago})
            idx += 1

    # --- Inspections (history back ~90 days) ------------------------------
    for r in robot_rows:
        d = r["last"]
        first = True
        while d < 90:
            if first and r["idx"] in INCIDENTS and INCIDENTS[r["idx"]][2] == "High":
                result = "Fail"
            else:
                result = rng.choices(["Pass", "Needs attention", "Fail"], [0.82, 0.14, 0.04])[0]
            kind = "Routine" if rng.random() < 0.8 else rng.choice(["Pre-deployment", "Safety audit"])
            conn.execute(
                "INSERT INTO inspections (robot_id, performed_on, inspector, kind, result, notes) VALUES (?, ?, ?, ?, ?, ?)",
                (r["id"], clock.fmt_date(today - timedelta(days=d)), rng.choice(TECHNICIANS), kind, result,
                 rng.choice(INSPECTION_NOTES[result])),
            )
            first = False
            d += r["interval"] + rng.randint(-2, 3)

    # --- Tickets ------------------------------------------------------------
    for r in robot_rows:
        if r["idx"] in INCIDENTS:
            _, title, prio, status, days_open = INCIDENTS[r["idx"]]
            conn.execute(
                """INSERT INTO tickets (robot_id, title, description, priority, status, assignee, opened_on)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (r["id"], title, "Reported by site lead.", prio, status, rng.choice(TECHNICIANS),
                 clock.fmt_ts(now - timedelta(days=days_open, hours=rng.randint(1, 6)))),
            )
    operational = [r for r in robot_rows if r["status"] in ("Operational", "Charging")]
    for r in rng.sample(operational, 6):
        title, prio = rng.choice(MINOR_ISSUES[r["model"]])
        conn.execute(
            """INSERT INTO tickets (robot_id, title, description, priority, status, assignee, opened_on)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (r["id"], title, "Observed during routine operation.", prio, rng.choice(["New", "In progress"]),
             rng.choice(TECHNICIANS), clock.fmt_ts(now - timedelta(days=rng.randint(0, 12), hours=rng.randint(0, 8)))),
        )
    # Closed history
    for _ in range(14):
        r = rng.choice(robot_rows)
        title, prio = rng.choice(MINOR_ISSUES[r["model"]])
        opened = now - timedelta(days=rng.randint(10, 60), hours=rng.randint(0, 8))
        conn.execute(
            """INSERT INTO tickets (robot_id, title, description, priority, status, assignee, opened_on, closed_on, resolution)
               VALUES (?, ?, ?, ?, 'Closed', ?, ?, ?, ?)""",
            (r["id"], title, "Observed during routine operation.", prio, rng.choice(TECHNICIANS), clock.fmt_ts(opened),
             clock.fmt_ts(opened + timedelta(days=rng.randint(0, 5), hours=rng.randint(1, 6))), "Replaced component and verified."),
        )

    _seed_teleop(conn, rng, robot_rows, now)

    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('seeded_at', ?)", (clock.fmt_ts(now),))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (db.SCHEMA_VERSION,))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('seed', ?)", (str(seed),))
    conn.commit()


TASKS_BY_MODEL = {
    "Heron": ["Pick and place", "Cloth folding", "Shelf restocking"],
    "Kestrel": ["Navigation mapping", "Docking practice"],
}


def _seed_teleop(conn: sqlite3.Connection, rng: random.Random, robot_rows: list[dict], now: datetime) -> None:
    """Teleoperation / data-collection sessions from 3 days ago to 4 days ahead.

    Each daily slot uses distinct robots and operators, so seeded sessions never conflict.
    Future sessions only use robots that are currently usable.
    """
    slots = [(9, 0, 2.0), (11, 30, 2.0), (14, 0, 2.0), (16, 30, 1.5)]
    for day_offset in range(-3, 5):
        day = datetime.combine(now.date() + timedelta(days=day_offset), datetime.min.time())
        if day.weekday() == 6:
            continue
        for hour, minute, hours in slots:
            start = day.replace(hour=hour, minute=minute)
            end = start + timedelta(hours=hours)
            pool = robot_rows if end <= now else [r for r in robot_rows if r["status"] in ("Operational", "Charging")]
            picks = rng.sample(pool, min(rng.randint(1, 2), len(pool)))
            for r, operator in zip(picks, rng.sample(OPERATORS, len(picks))):
                target = int(hours * rng.choice([15, 20, 25]))
                if end <= now:
                    status = "Completed" if rng.random() < 0.9 else "Cancelled"
                    collected = int(target * rng.uniform(0.6, 1.1)) if status == "Completed" else 0
                else:
                    status, collected = ("In progress" if start <= now else "Scheduled"), 0
                conn.execute(
                    """INSERT INTO teleop_sessions (robot_id, operator, task, start_at, end_at, target_episodes,
                                                    collected_episodes, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (r["id"], operator, rng.choice(TASKS_BY_MODEL[r["model"]]), clock.fmt_ts(start), clock.fmt_ts(end),
                     target, collected, status),
                )
