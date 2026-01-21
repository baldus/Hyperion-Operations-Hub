"""Collector for app info and uptime."""
from __future__ import annotations

from datetime import datetime, timezone
import os
import subprocess
import time
from typing import Any

from ..config import Config


def collect(start_time: float, config: Config) -> dict[str, Any]:
    uptime_sec = max(0.0, _now() - start_time)
    version = os.getenv("HYPERION_VERSION", "unknown")
    commit = os.getenv("HYPERION_COMMIT", "") or _git_commit()
    details = f"Uptime {int(uptime_sec)}s"
    if version != "unknown":
        details += f", version {version}"
    if commit:
        details += f", commit {commit[:7]}"
    return {
        "ok": True,
        "status": "OK",
        "details": details,
        "metrics": {
            "uptime_sec": round(uptime_sec, 2),
            "version": version,
            "commit": commit,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def _now() -> float:
    return time.monotonic()


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()
