import httpx
import pytest

from robotops import github

TICKET = {"id": 3, "robot": "HRN-003", "title": "Wrist camera dropout", "priority": "High", "status": "New",
          "assignee": "Hana Sato", "opened_on": "2026-11-10 14:28", "description": "Drops frames."}
LIVE = github.GitHubConfig(repo="octo/robots", token="ghp_TOPSECRET", dry_run=False, public_demo=False)
DRAFT = github.build_issue(TICKET)


def client_returning(status, json=None, headers=None, seen=None):
    def handler(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=json or {}, headers=headers or {})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_config_defaults_to_dry_run(monkeypatch):
    for k in ("GITHUB_TOKEN", "ROBOTOPS_GITHUB_REPO", "ROBOTOPS_GITHUB_DRY_RUN"):
        monkeypatch.delenv(k, raising=False)
    assert github.GitHubConfig.from_env().mode == "dry_run"
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("ROBOTOPS_GITHUB_REPO", "o/r")
    assert github.GitHubConfig.from_env().mode == "dry_run"  # still dry-run until explicitly disabled
    monkeypatch.setenv("ROBOTOPS_GITHUB_DRY_RUN", "false")
    assert github.GitHubConfig.from_env().mode == "dry_run"  # public demo mode never goes live
    assert "public demo" in github.GitHubConfig.from_env().dry_run_reason
    monkeypatch.setenv("ROBOTOPS_MODE", "shared")
    assert github.GitHubConfig.from_env().mode == "live"
    monkeypatch.delenv("GITHUB_TOKEN")
    assert github.GitHubConfig.from_env().mode == "dry_run"  # live needs a token


def test_token_never_exposed():
    assert "TOPSECRET" not in repr(LIVE) and "TOPSECRET" not in str(LIVE.public())


def test_draft_contents():
    assert DRAFT.title == "[RobotOps] HRN-003: Wrist camera dropout"
    assert "priority:high" in DRAFT.labels and "synthetic" in DRAFT.body and "#3" in DRAFT.body


def test_requires_approval():
    with pytest.raises(github.GitHubError) as e:
        github.create_issue(DRAFT, LIVE, approved=False)
    assert e.value.status == 403


def test_dry_run_makes_no_request():
    seen = []
    res = github.create_issue(DRAFT, LIVE, approved=True, force_dry_run=True, client=client_returning(201, seen=seen))
    assert res.dry_run and res.url is None and not seen
    res = github.create_issue(DRAFT, github.GitHubConfig(), approved=True, client=client_returning(201, seen=seen))
    assert res.dry_run and not seen


def test_live_success_sends_expected_request():
    seen = []
    res = github.create_issue(DRAFT, LIVE, approved=True,
                              client=client_returning(201, {"html_url": "https://github.com/octo/robots/issues/9", "number": 9}, seen=seen))
    assert not res.dry_run and res.number == 9
    req = seen[0]
    assert req.url == "https://api.github.com/repos/octo/robots/issues"
    assert req.headers["Authorization"] == "Bearer ghp_TOPSECRET"
    assert b"Wrist camera dropout" in req.content


@pytest.mark.parametrize("status, headers, fragment", [
    (401, {}, "rejected the token"),
    (403, {"x-ratelimit-remaining": "0"}, "rate limit"),
    (403, {}, "lacks permission"),
    (404, {}, "not found"),
    (422, {}, "invalid"),
    (500, {}, "unexpected status"),
])
def test_error_mapping(status, headers, fragment):
    with pytest.raises(github.GitHubError) as e:
        github.create_issue(DRAFT, LIVE, approved=True, client=client_returning(status, headers=headers))
    assert fragment in str(e.value) and "TOPSECRET" not in str(e.value)


def test_timeout_and_network_errors():
    def boom(exc):
        def handler(request):
            raise exc
        return httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(github.GitHubError) as e:
        github.create_issue(DRAFT, LIVE, approved=True, client=boom(httpx.ReadTimeout("slow")))
    assert e.value.status == 504
    with pytest.raises(github.GitHubError) as e:
        github.create_issue(DRAFT, LIVE, approved=True, client=boom(httpx.ConnectError("ghp_TOPSECRET refused")))
    assert e.value.status == 502 and "TOPSECRET" not in str(e.value)


def test_bad_repo_name_rejected():
    with pytest.raises(github.GitHubError):
        github.create_issue(DRAFT, github.GitHubConfig(repo="not a repo", token="t", dry_run=False, public_demo=False), approved=True)
