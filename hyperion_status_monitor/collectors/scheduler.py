"""Collector for scheduler ticks."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..config import Config


def collect(config: Config) -> dict[str, Any]:
    tick_path = config.scheduler_tick_path
    if not tick_path:
        return {
            "ok": False,
            "status": "WARN",
            "details": "Scheduler tick path not configured.",
            "metrics": {},
        }
    if not tick_path.exists():
        return {
            "ok": False,
            "status": "WARN",
            "details": f"Scheduler tick file missing: {tick_path}",
            "metrics": {},
        }
    try:
        contents = tick_path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        return {
            "ok": False,
            "status": "ERROR",
            "details": f"Unable to read scheduler tick: {exc}",
            "metrics": {},
        }
    details = "Scheduler tick observed."
    metrics: dict[str, Any] = {"tick": contents}
    try:
        tick_dt = datetime.fromisoformat(contents)
        if tick_dt.tzinfo is None:
            tick_dt = tick_dt.replace(tzinfo=timezone.utc)
        age_sec = (datetime.now(timezone.utc) - tick_dt).total_seconds()
        metrics["age_sec"] = round(age_sec, 2)
    except ValueError:
        details = "Scheduler tick present but not ISO8601."
    return {
        "ok": True,
        "status": "OK",
        "details": details,
        "metrics": metrics,
    }
