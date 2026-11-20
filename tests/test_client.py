import httpx
import pytest

from robotops.client import ApiError, RobotOpsClient


def make(handler):
    return RobotOpsClient("http://api.test", "a" * 32, api_key="k", transport=httpx.MockTransport(handler))


def test_sends_session_and_key_headers():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"kpis": {}, "robots": []})

    make(handler).fleet_status()
    assert seen[0].headers["X-RobotOps-Session"] == "a" * 32 and seen[0].headers["X-API-Key"] == "k"


def test_surfaces_api_detail():
    c = make(lambda req: httpx.Response(404, json={"detail": "Unknown robot 'X'."}))
    with pytest.raises(ApiError, match="Unknown robot") as e:
        c.create_issue("X", "t", "Low", "Ana")
    assert e.value.status == 404


def test_formats_validation_errors():
    detail = [{"loc": ["body", "priority"], "msg": "Input should be 'Low'"}]
    with pytest.raises(ApiError, match="priority: Input should be"):
        make(lambda req: httpx.Response(422, json={"detail": detail})).create_issue("KST-001", "t", "Bad", "Ana")


@pytest.mark.parametrize("exc, fragment", [
    (httpx.ConnectError("refused"), "Cannot reach"),
    (httpx.ReadTimeout("slow"), "timed out"),
])
def test_network_failures(exc, fragment):
    def handler(req):
        raise exc

    with pytest.raises(ApiError, match=fragment):
        make(handler).fleet_status()


def test_non_json_response():
    with pytest.raises(ApiError, match="invalid response"):
        make(lambda req: httpx.Response(200, text="<html>")).health()


def test_real_unreachable_server():
    with pytest.raises(ApiError, match="Cannot reach"):
        RobotOpsClient("http://127.0.0.1:9", "a" * 32, timeout=2).health()
