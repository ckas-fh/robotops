"""Run the RobotOps API inside the Streamlit process when no external API URL is configured."""

from __future__ import annotations

import os
import socket
import threading
import time

import uvicorn

from api.main import app

_embedded_url: str | None = None
_lock = threading.Lock()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def embedded_api_url(timeout: float = 15.0) -> str:
    """Start the FastAPI app with uvicorn on a localhost port in a daemon thread (once per process).

    Used when ROBOTOPS_API_URL is not set, so a single `streamlit run` still exercises
    the real REST API over HTTP.
    """
    global _embedded_url
    with _lock:
        if _embedded_url:
            return _embedded_url
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True, name="robotops-api").start()
        deadline = time.monotonic() + timeout
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("Embedded RobotOps API failed to start.")
            time.sleep(0.05)
        _embedded_url = f"http://127.0.0.1:{port}"
        return _embedded_url


def resolve_api_url() -> tuple[str, bool]:
    """Return (base_url, embedded?)."""
    url = os.environ.get("ROBOTOPS_API_URL", "").strip()
    return (url, False) if url else (embedded_api_url(), True)
