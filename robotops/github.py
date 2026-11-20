"""Optional GitHub Issues integration for maintenance tickets.

Safety model
------------
* **Dry-run by default.** Nothing is sent to GitHub unless ``ROBOTOPS_GITHUB_DRY_RUN=false``
  *and* both ``GITHUB_TOKEN`` and ``ROBOTOPS_GITHUB_REPO`` are set. Otherwise every request
  returns the exact payload that *would* have been sent.
* **Never live in a public demo.** Live mode also requires ``ROBOTOPS_MODE=shared``: in the
  public per-visitor sandbox any anonymous visitor could otherwise approve issue creation.
* **Human approval.** :func:`create_issue` refuses to run unless ``approved=True``; the UI
  only sets that after a person has reviewed the preview and ticked an approval box.
* **Credential hygiene.** The token is read from the environment on the API server only,
  is never returned by any endpoint, never logged, and is redacted from error messages
  and ``repr()``. Use a fine-grained token scoped to one repository with *Issues: write*.
* **Synthetic data.** Issue bodies state that the robot data is simulated.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import httpx

API_ROOT = "https://api.github.com"
TIMEOUT_SECONDS = 10.0
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TRUE = {"1", "true", "yes", "on"}


class GitHubError(RuntimeError):
    """A user-presentable GitHub failure. ``status`` is the HTTP status to surface (502 for upstream errors)."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class GitHubConfig:
    repo: str = ""
    token: str = field(default="", repr=False)
    dry_run: bool = True
    public_demo: bool = True

    @classmethod
    def from_env(cls) -> "GitHubConfig":
        return cls(
            repo=os.environ.get("ROBOTOPS_GITHUB_REPO", "").strip(),
            token=os.environ.get("GITHUB_TOKEN", "").strip(),
            dry_run=os.environ.get("ROBOTOPS_GITHUB_DRY_RUN", "true").strip().lower() in _TRUE,
            public_demo=os.environ.get("ROBOTOPS_MODE", "demo").strip().lower() != "shared",
        )

    @property
    def mode(self) -> str:
        """``live`` only when fully configured and dry-run is explicitly disabled."""
        return "dry_run" if self.dry_run_reason else "live"

    @property
    def dry_run_reason(self) -> str | None:
        """Why the integration is in dry-run mode, or None when live."""
        if self.dry_run:
            return "ROBOTOPS_GITHUB_DRY_RUN is not set to false"
        if not (self.token and self.repo):
            return "GITHUB_TOKEN or ROBOTOPS_GITHUB_REPO is not set"
        if self.public_demo:
            return "live mode is disabled in the public demo; set ROBOTOPS_MODE=shared for a private deployment"
        return None

    def public(self) -> dict:
        """Safe-to-expose description of the configuration (never includes the token)."""
        return {
            "mode": self.mode,
            "repo": self.repo or None,
            "token_configured": bool(self.token),
            "dry_run_requested": self.dry_run,
            "dry_run_reason": self.dry_run_reason,
        }


@dataclass(frozen=True)
class IssueDraft:
    title: str
    body: str
    labels: list[str]


@dataclass(frozen=True)
class IssueResult:
    dry_run: bool
    repo: str | None
    draft: IssueDraft
    url: str | None = None
    number: int | None = None


def build_issue(ticket: dict) -> IssueDraft:
    """Turn a maintenance ticket (as returned by the API) into a GitHub issue draft."""
    title = f"[RobotOps] {ticket['robot']}: {ticket['title']}"[:256]
    body = "\n".join([
        f"- **Robot:** {ticket['robot']}",
        f"- **Priority:** {ticket['priority']}",
        f"- **Status:** {ticket['status']}",
        f"- **Assignee:** {ticket['assignee']}",
        f"- **Opened:** {ticket['opened_on']}",
        f"- **RobotOps ticket:** #{ticket['id']}",
        "",
        ticket.get("description") or "_No description._",
        "",
        "---",
        "_Created from RobotOps after human approval. All robot data is synthetic; no physical hardware is involved._",
    ])
    labels = ["robotops", "maintenance", f"priority:{ticket['priority'].lower()}"]
    return IssueDraft(title=title, body=body, labels=labels)


def _redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def create_issue(
    draft: IssueDraft,
    config: GitHubConfig,
    *,
    approved: bool,
    force_dry_run: bool = False,
    client: httpx.Client | None = None,
) -> IssueResult:
    if not approved:
        raise GitHubError("Creating a GitHub issue requires explicit human approval.", status=403)
    if force_dry_run or config.mode != "live":
        return IssueResult(dry_run=True, repo=config.repo or None, draft=draft)
    if not _REPO_RE.match(config.repo):
        raise GitHubError("ROBOTOPS_GITHUB_REPO must look like 'owner/repo'.", status=500)

    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        resp = client.post(
            f"{API_ROOT}/repos/{config.repo}/issues",
            json={"title": draft.title, "body": draft.body, "labels": draft.labels},
            headers={
                "Authorization": f"Bearer {config.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "robotops-demo",
            },
        )
    except httpx.TimeoutException as exc:
        raise GitHubError("GitHub did not respond in time. Nothing was created; try again.", status=504) from exc
    except httpx.HTTPError as exc:
        raise GitHubError(f"Could not reach GitHub: {_redact(type(exc).__name__, config.token)}.", status=502) from exc
    finally:
        if owns_client:
            client.close()

    if resp.status_code == 201:
        data = resp.json()
        return IssueResult(dry_run=False, repo=config.repo, draft=draft, url=data.get("html_url"), number=data.get("number"))

    if resp.status_code == 401:
        msg = "GitHub rejected the token (401). Check GITHUB_TOKEN on the API server."
    elif resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
        msg = "GitHub rate limit reached (403). Try again later."
    elif resp.status_code == 403:
        msg = "The token lacks permission to create issues in this repository (403)."
    elif resp.status_code == 404:
        msg = f"Repository '{config.repo}' not found, or the token cannot see it (404)."
    elif resp.status_code == 410:
        msg = "Issues are disabled for this repository (410)."
    elif resp.status_code == 422:
        msg = "GitHub rejected the issue as invalid (422)."
    else:
        msg = f"GitHub returned an unexpected status ({resp.status_code})."
    raise GitHubError(_redact(msg, config.token), status=502)
