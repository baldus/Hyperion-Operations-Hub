"""Collector for the main app health ping."""
from __future__ import annotations

from typing import Any
import time
from urllib import request, error

from ..config import Config


def collect(config: Config) -> dict[str, Any]:
    if not config.main_app_health_url:
        return {
            "ok": False,
            "status": "WARN",
            "details": "MAIN_APP_HEALTH_URL not configured.",
            "metrics": {},
        }
    start = time.monotonic()
    try:
        req = request.Request(config.main_app_health_url, method="GET")
        with request.urlopen(req, timeout=2) as response:
            status_code = response.status
        latency_ms = (time.monotonic() - start) * 1000
    except error.URLError as exc:
        return {
            "ok": False,
            "status": "ERROR",
            "details": f"Health check failed: {exc}",
            "metrics": {},
        }
    ok = 200 <= status_code < 300
    return {
        "ok": ok,
        "status": "OK" if ok else "WARN",
        "details": f"Status {status_code} in {latency_ms:.0f}ms",
        "metrics": {
            "status_code": status_code,
            "latency_ms": round(latency_ms, 2),
        },
    }
