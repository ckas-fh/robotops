import warnings

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.main import app

A, B = "a" * 32, "b" * 32
H = {"X-RobotOps-Session": A}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_is_public_and_hides_token(client, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_supersecret")
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["github"]["token_configured"] is True
    assert "ghp_supersecret" not in str(body)


def test_fleet_status(client):
    body = client.get("/api/v1/fleet/status", headers=H).json()
    assert body["kpis"]["fleet_size"] == 20 and len(body["robots"]) == 20
    assert {"name", "status", "battery_pct", "next_inspection", "inspection_state"} <= body["robots"][0].keys()


@pytest.mark.parametrize("headers, status", [({}, 400), ({"X-RobotOps-Session": "../etc"}, 400)])
def test_session_header_validated(client, headers, status):
    assert client.get("/api/v1/fleet/status", headers=headers).status_code == status


def test_sessions_isolated(client):
    client.patch("/api/v1/robots/KST-001/status", headers=H, json={"status": "Down"})
    a = {r["name"]: r["status"] for r in client.get("/api/v1/fleet/status", headers=H).json()["robots"]}
    b = {r["name"]: r["status"] for r in client.get("/api/v1/fleet/status", headers={"X-RobotOps-Session": B}).json()["robots"]}
    assert a["KST-001"] == "Down" and b["KST-001"] != "Down"


def test_issue_lifecycle(client):
    r = client.post("/api/v1/issues", headers=H, json={"robot": "HRN-002", "title": "Gripper slip", "priority": "Critical", "assignee": "Ana"})
    assert r.status_code == 201
    tid = r.json()["id"]
    robots = {x["name"]: x["status"] for x in client.get("/api/v1/fleet/status", headers=H).json()["robots"]}
    assert robots["HRN-002"] == "Down"
    assert any(i["id"] == tid for i in client.get("/api/v1/issues", headers=H, params={"priority": "Critical"}).json()["issues"])
    # Closing requires a resolution
    assert client.patch(f"/api/v1/issues/{tid}", headers=H, json={"status": "Closed"}).status_code == 400
    r = client.patch(f"/api/v1/issues/{tid}", headers=H, json={"status": "Closed", "resolution": "Replaced pads"})
    assert r.status_code == 200 and r.json()["issue"]["status"] == "Closed"
    assert client.patch(f"/api/v1/issues/{tid}", headers=H, json={"status": "New"}).status_code == 409


def test_validation_and_not_found(client):
    assert client.post("/api/v1/issues", headers=H, json={"robot": "NOPE", "title": "x"}).status_code == 404
    assert client.post("/api/v1/issues", headers=H, json={"robot": "KST-001", "title": "x", "priority": "Urgent"}).status_code == 422
    assert client.patch("/api/v1/robots/KST-001/status", headers=H, json={"status": "Exploded"}).status_code == 422
    assert client.get("/api/v1/issues/9999", headers=H).status_code == 404


def test_inspection_fail_creates_ticket(client):
    r = client.post("/api/v1/inspections", headers=H, json={"robot": "KST-004", "inspector": "Ana", "result": "Fail"})
    assert r.status_code == 201 and r.json()["ticket_id"]


def test_teleop_sessions(client):
    for window in ("past", "today", "upcoming", "all"):
        body = client.get("/api/v1/teleop/sessions", headers=H, params={"window": window}).json()
        assert body["count"] == len(body["sessions"])
    past = client.get("/api/v1/teleop/sessions", headers=H, params={"window": "past"}).json()["sessions"]
    assert past and {"robot", "operator", "task", "start_at", "status"} <= past[0].keys()
    assert client.get("/api/v1/teleop/sessions", headers=H, params={"window": "soon"}).status_code == 422


def test_reset(client):
    client.patch("/api/v1/robots/KST-001/status", headers=H, json={"status": "Down"})
    client.post("/api/v1/session/reset", headers=H)
    robots = {r["name"]: r["status"] for r in client.get("/api/v1/fleet/status", headers=H).json()["robots"]}
    assert robots["KST-001"] != "Down"


def test_api_key_enforced_when_configured(client, monkeypatch):
    monkeypatch.setenv("ROBOTOPS_API_KEY", "k3y")
    assert client.get("/api/v1/fleet/status", headers=H).status_code == 401
    assert client.get("/api/v1/fleet/status", headers={**H, "X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/v1/fleet/status", headers={**H, "X-API-Key": "k3y"}).status_code == 200
    assert client.get("/health").status_code == 200


# --- GitHub endpoints (no network: default config is dry-run) ---------------------------------
def test_github_preview_and_dry_run(client):
    p = client.get("/api/v1/issues/1/github/preview", headers=H).json()
    assert p["config"]["mode"] == "dry_run" and p["draft"]["title"].startswith("[RobotOps]")
    assert "synthetic" in p["draft"]["body"]
    r = client.post("/api/v1/issues/1/github", headers=H, json={"approved": True}).json()
    assert r["dry_run"] is True and r["url"] is None
    # dry run does not link the ticket
    assert client.get("/api/v1/issues/1", headers=H).json()["github_issue_url"] is None


def test_github_requires_approval(client):
    r = client.post("/api/v1/issues/1/github", headers=H, json={"approved": False})
    assert r.status_code == 403
    assert client.post("/api/v1/issues/1/github", headers=H, json={}).status_code == 422


def test_github_live_links_ticket_once(client, monkeypatch):
    from robotops import github

    calls = []

    def fake_create(draft, cfg, *, approved, force_dry_run=False, client=None):
        calls.append(cfg)
        return github.IssueResult(dry_run=False, repo=cfg.repo, draft=draft, url="https://github.com/o/r/issues/7", number=7)

    monkeypatch.setattr(github, "create_issue", fake_create)
    r = client.post("/api/v1/issues/2/github", headers=H, json={"approved": True})
    assert r.json()["url"].endswith("/7")
    assert client.get("/api/v1/issues/2", headers=H).json()["github_issue_url"].endswith("/7")
    assert client.post("/api/v1/issues/2/github", headers=H, json={"approved": True}).status_code == 409
    assert len(calls) == 1


def test_unexpected_errors_are_generic(client, monkeypatch):
    from robotops import queries

    monkeypatch.setattr(queries, "fleet_kpis", lambda *a, **k: 1 / 0)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/v1/fleet/status", headers=H)
    assert r.status_code == 500 and r.json() == {"detail": "Internal server error."}
