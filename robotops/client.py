"""HTTP client for the RobotOps REST API (used by the Streamlit UI).

Every call raises :class:`ApiError` with a user-presentable message on failure:
connection refused, timeouts, non-JSON responses, and 4xx/5xx with the API's
``detail`` message.
"""

from __future__ import annotations

from datetime import date

import httpx

DEFAULT_TIMEOUT = 10.0


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RobotOpsClient:
    def __init__(self, base_url: str, session_id: str, api_key: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT, transport: httpx.BaseTransport | None = None):
        headers = {"X-RobotOps-Session": session_id, "User-Agent": "robotops-ui"}
        if api_key:
            headers["X-API-Key"] = api_key
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs):
        try:
            resp = self._http.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise ApiError(f"Cannot reach the RobotOps API at {self.base_url}. Is it running?") from exc
        except httpx.TimeoutException as exc:
            raise ApiError("The RobotOps API timed out. Please try again.") from exc
        except httpx.HTTPError as exc:
            raise ApiError(f"Network error talking to the RobotOps API ({type(exc).__name__}).") from exc
        try:
            data = resp.json()
        except ValueError:
            data = None
        if resp.is_error:
            detail = data.get("detail") if isinstance(data, dict) else None
            if isinstance(detail, list):  # FastAPI validation errors
                detail = "; ".join(f"{'.'.join(map(str, d.get('loc', [])[1:]))}: {d.get('msg')}" for d in detail)
            raise ApiError(detail or f"API error ({resp.status_code}).", resp.status_code)
        if data is None:
            raise ApiError("The RobotOps API returned an invalid response.", resp.status_code)
        return data

    # --- meta
    def health(self) -> dict:
        return self._request("GET", "/health")

    def reset(self) -> dict:
        return self._request("POST", "/api/v1/session/reset")

    # --- fleet
    def fleet_status(self) -> dict:
        return self._request("GET", "/api/v1/fleet/status")

    def set_robot_status(self, robot: str, status: str) -> dict:
        return self._request("PATCH", f"/api/v1/robots/{robot}/status", json={"status": status})

    # --- issues / inspections
    def issues(self, include_closed: bool = False) -> list[dict]:
        return self._request("GET", "/api/v1/issues", params={"include_closed": include_closed})["issues"]

    def create_issue(self, robot: str, title: str, priority: str, assignee: str, description: str = "") -> dict:
        return self._request("POST", "/api/v1/issues", json={
            "robot": robot, "title": title, "priority": priority, "assignee": assignee, "description": description})

    def update_issue(self, ticket_id: int, status: str, assignee: str | None = None, resolution: str = "") -> dict:
        return self._request("PATCH", f"/api/v1/issues/{ticket_id}", json={
            "status": status, "assignee": assignee, "resolution": resolution})

    def log_inspection(self, robot: str, inspector: str, kind: str, result: str, notes: str = "",
                       performed_on: date | None = None) -> dict:
        return self._request("POST", "/api/v1/inspections", json={
            "robot": robot, "inspector": inspector, "kind": kind, "result": result, "notes": notes,
            "performed_on": performed_on.isoformat() if performed_on else None})

    # --- teleop
    def teleop_sessions(self, window: str = "upcoming", days: int = 7) -> dict:
        return self._request("GET", "/api/v1/teleop/sessions", params={"window": window, "days": days})

    # --- github
    def github_config(self) -> dict:
        return self._request("GET", "/api/v1/integrations/github")

    def github_preview(self, ticket_id: int) -> dict:
        return self._request("GET", f"/api/v1/issues/{ticket_id}/github/preview")

    def github_create(self, ticket_id: int, approved: bool, dry_run: bool = False) -> dict:
        return self._request("POST", f"/api/v1/issues/{ticket_id}/github", json={"approved": approved, "dry_run": dry_run})
