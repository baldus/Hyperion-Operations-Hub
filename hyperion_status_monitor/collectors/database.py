"""Collector for database connectivity."""
from __future__ import annotations

from typing import Any
import time

from sqlalchemy import create_engine, text

from ..config import Config


def collect(config: Config) -> dict[str, Any]:
    if not config.database_url:
        return {
            "ok": False,
            "status": "WARN",
            "details": "DATABASE_URL not configured.",
            "metrics": {},
        }

    start = time.monotonic()
    engine = create_engine(
        config.database_url,
        pool_pre_ping=False,
        connect_args={"connect_timeout": 1},
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "ok": True,
            "status": "OK",
            "details": f"Connected in {latency_ms:.0f}ms",
            "metrics": {"latency_ms": round(latency_ms, 2)},
        }
    finally:
        engine.dispose()
