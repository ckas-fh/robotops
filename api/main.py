"""RobotOps REST API (FastAPI).

Serves the simulated fleet, maintenance issues, inspections and teleoperation
sessions, plus the optional human-approved GitHub Issues integration.

Run:  uvicorn api.main:app --port 8000      (docs at http://localhost:8000/docs)

Data isolation: in demo mode each caller passes ``X-RobotOps-Session`` (32 hex chars)
and gets its own synthetic SQLite database. In shared mode the header is ignored.
Optional auth: when ``ROBOTOPS_API_KEY`` is set, every /api/v1 request must send it
as ``X-API-Key``.
"""

from __future__ import annotations

import logging
import math
import os
import secrets
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from robotops import __version__, clock, db, github, maintenance, queries, session
from robotops.constants import (
    INSPECTION_KINDS,
    INSPECTION_RESULTS,
    ROBOT_STATUSES,
    TELEOP_STATUSES,
    TICKET_PRIORITIES,
    TICKET_STATUSES,
)

log = logging.getLogger("robotops.api")

RobotStatus = Literal[tuple(ROBOT_STATUSES)]  # type: ignore[valid-type]
Priority = Literal[tuple(TICKET_PRIORITIES)]  # type: ignore[valid-type]
TicketStatus = Literal[tuple(TICKET_STATUSES)]  # type: ignore[valid-type]
InspectionKind = Literal[tuple(INSPECTION_KINDS)]  # type: ignore[valid-type]
InspectionResult = Literal[tuple(INSPECTION_RESULTS)]  # type: ignore[valid-type]
TeleopStatus = Literal[tuple(TELEOP_STATUSES)]  # type: ignore[valid-type]

app = FastAPI(
    title="RobotOps API",
    version=__version__,
    description="Synthetic robotics operations API. All robot data is simulated; no hardware is connected.",
)


# --- Error handling -----------------------------------------------------------------
@app.exception_handler(maintenance.MaintenanceError)
async def _maintenance_error(_: Request, exc: maintenance.MaintenanceError):
    status = 404 if str(exc).startswith("Unknown") else 409 if "already" in str(exc) else 400
    return JSONResponse(status_code=status, content={"detail": str(exc)})


@app.exception_handler(github.GitHubError)
async def _github_error(_: Request, exc: github.GitHubError):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def _unexpected(_: Request, exc: Exception):
    log.exception("Unhandled API error")  # details stay in server logs, not in responses
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


# --- Dependencies ---------------------------------------------------------------------
def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = os.environ.get("ROBOTOPS_API_KEY")
    if expected and not (x_api_key and secrets.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key.")


def db_path(x_robotops_session: Annotated[str | None, Header()] = None):
    sid = x_robotops_session or ""
    if session.mode() == "demo" and not sid:
        raise HTTPException(status_code=400, detail="X-RobotOps-Session header is required in demo mode.")
    try:
        return session.db_path(sid)
    except ValueError:
        raise HTTPException(status_code=400, detail="X-RobotOps-Session must be 32 lowercase hex characters.")


DbPath = Annotated[object, Depends(db_path)]


def _clean(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if hasattr(v, "item"):  # numpy scalars
        return v.item()
    return v


def records(df) -> list[dict]:
    return [{k: _clean(v) for k, v in row.items()} for row in df.to_dict(orient="records")]


def _robot_id(conn, name: str) -> int:
    row = conn.execute("SELECT id FROM robots WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown robot {name!r}.")
    return row["id"]


def _ticket(conn, ticket_id: int) -> dict:
    df = queries.tickets(conn)
    match = df[df["id"] == ticket_id]
    if match.empty:
        raise HTTPException(status_code=404, detail=f"Unknown ticket #{ticket_id}.")
    return records(match)[0]


# --- Request bodies ---------------------------------------------------------------------
class RobotStatusUpdate(BaseModel):
    status: RobotStatus


class IssueCreate(BaseModel):
    robot: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    priority: Priority = "Medium"
    assignee: str = Field(default="Unassigned", max_length=80)
    description: str = Field(default="", max_length=4000)


class IssueUpdate(BaseModel):
    status: TicketStatus
    assignee: str | None = Field(default=None, max_length=80)
    resolution: str = Field(default="", max_length=4000, description="Required when status is Closed.")


class InspectionCreate(BaseModel):
    robot: str = Field(min_length=1, max_length=40)
    inspector: str = Field(min_length=1, max_length=80)
    kind: InspectionKind = "Routine"
    result: InspectionResult
    notes: str = Field(default="", max_length=4000)
    performed_on: date | None = None


class GitHubIssueRequest(BaseModel):
    approved: bool = Field(description="Must be true: confirms a human reviewed the preview.")
    dry_run: bool = Field(default=False, description="Force a dry run even if the server is configured for live mode.")


# --- Routes -----------------------------------------------------------------------------
@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "version": __version__, "data_mode": session.mode(), "github": github.GitHubConfig.from_env().public()}


v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@v1.get("/fleet/status", tags=["fleet"])
def fleet_status(path: DbPath):
    """Fleet KPIs plus every robot's current state."""
    with db.transaction(path) as c:
        return {"generated_at": clock.fmt_ts(clock.now()), "kpis": queries.fleet_kpis(c), "robots": records(queries.robots(c))}


@v1.patch("/robots/{name}/status", tags=["fleet"])
def set_robot_status(name: str, body: RobotStatusUpdate, path: DbPath):
    with db.transaction(path) as c:
        maintenance.set_robot_status(c, _robot_id(c, name), body.status)
    return {"robot": name, "status": body.status}


@v1.get("/issues", tags=["issues"])
def list_issues(
    path: DbPath,
    include_closed: bool = False,
    priority: Priority | None = None,
    status: TicketStatus | None = None,
    robot: str | None = None,
):
    """Robot maintenance issues (tickets). Open issues only unless include_closed=true."""
    with db.transaction(path) as c:
        df = queries.tickets(c, include_closed=include_closed)
    if priority:
        df = df[df["priority"] == priority]
    if status:
        df = df[df["status"] == status]
    if robot:
        df = df[df["robot"] == robot]
    return {"count": len(df), "issues": records(df)}


@v1.get("/issues/{ticket_id}", tags=["issues"])
def get_issue(ticket_id: int, path: DbPath):
    with db.transaction(path) as c:
        return _ticket(c, ticket_id)


@v1.post("/issues", tags=["issues"], status_code=201)
def create_issue(body: IssueCreate, path: DbPath):
    with db.transaction(path) as c:
        tid = maintenance.create_ticket(c, _robot_id(c, body.robot), body.title, body.priority, body.assignee, body.description)
        return _ticket(c, tid)


@v1.patch("/issues/{ticket_id}", tags=["issues"])
def update_issue(ticket_id: int, body: IssueUpdate, path: DbPath):
    """Change status/assignee. Setting status to Closed requires a resolution."""
    with db.transaction(path) as c:
        _ticket(c, ticket_id)
        messages: list[str] = []
        if body.status == "Closed":
            messages = maintenance.close_ticket(c, ticket_id, body.resolution)
        else:
            maintenance.update_ticket(c, ticket_id, body.status, body.assignee)
        return {"issue": _ticket(c, ticket_id), "messages": messages}


@v1.post("/inspections", tags=["inspections"], status_code=201)
def log_inspection(body: InspectionCreate, path: DbPath):
    with db.transaction(path) as c:
        res = maintenance.log_inspection(c, _robot_id(c, body.robot), body.inspector, body.kind, body.result, body.notes, body.performed_on)
    return {"inspection_id": res.inspection_id, "ticket_id": res.ticket_id, "messages": res.messages}


@v1.get("/teleop/sessions", tags=["teleop"])
def teleop_sessions(
    path: DbPath,
    window: Literal["past", "today", "upcoming", "all"] = "upcoming",
    status: TeleopStatus | None = None,
    robot: str | None = None,
    days: int = Query(default=7, ge=1, le=30),
):
    """Simulated teleoperation / data-collection sessions."""
    now = clock.now()
    midnight = datetime.combine(now.date(), time())
    start, end = {
        "past": (now - timedelta(days=days), now),
        "today": (midnight, midnight + timedelta(days=1)),
        "upcoming": (now, now + timedelta(days=days)),
        "all": (None, None),
    }[window]
    with db.transaction(path) as c:
        df = queries.teleop_sessions(c, start, end, status, robot)
    return {"window": window, "count": len(df), "sessions": records(df)}


@v1.post("/session/reset", tags=["meta"])
def reset_data(x_robotops_session: Annotated[str | None, Header()] = None):
    """Re-seed the caller's synthetic dataset (in shared mode: the shared dataset)."""
    db_path(x_robotops_session)  # validates the header
    session.reset(x_robotops_session or "")
    return {"reset": True}


@v1.get("/integrations/github", tags=["github"])
def github_config():
    """Current GitHub integration mode. Never exposes the token."""
    return github.GitHubConfig.from_env().public()


@v1.get("/issues/{ticket_id}/github/preview", tags=["github"])
def github_preview(ticket_id: int, path: DbPath):
    """Exactly what would be sent to GitHub, for human review before approval."""
    cfg = github.GitHubConfig.from_env()
    with db.transaction(path) as c:
        ticket = _ticket(c, ticket_id)
    draft = github.build_issue(ticket)
    return {"config": cfg.public(), "already_linked": ticket["github_issue_url"],
            "draft": {"title": draft.title, "body": draft.body, "labels": draft.labels}}


@v1.post("/issues/{ticket_id}/github", tags=["github"])
def github_create(ticket_id: int, body: GitHubIssueRequest, path: DbPath):
    """Create a GitHub issue for a ticket. Requires approved=true; dry-run unless the server is in live mode."""
    cfg = github.GitHubConfig.from_env()
    with db.transaction(path) as c:
        ticket = _ticket(c, ticket_id)
    if ticket["github_issue_url"]:
        raise HTTPException(status_code=409, detail=f"Ticket #{ticket_id} is already linked to {ticket['github_issue_url']}.")
    result = github.create_issue(github.build_issue(ticket), cfg, approved=body.approved, force_dry_run=body.dry_run)
    if not result.dry_run and result.url:
        with db.transaction(path) as c:
            maintenance.link_github_issue(c, ticket_id, result.url)
    return {
        "dry_run": result.dry_run,
        "repo": result.repo,
        "url": result.url,
        "number": result.number,
        "draft": {"title": result.draft.title, "body": result.draft.body, "labels": result.draft.labels},
    }


app.include_router(v1)
