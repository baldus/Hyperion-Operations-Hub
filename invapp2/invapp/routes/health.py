from __future__ import annotations

from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify


bp = Blueprint("health", __name__, url_prefix="/health")
_STATUS_PATH = Path("/var/lib/hyperion/network_status.txt")


def _parse_status_line(line: str) -> tuple[str, str | None]:
    parts = [part.strip() for part in line.split("|")]
    status = parts[0] if parts and parts[0] else "UNKNOWN"
    last_updated = None
    if len(parts) > 1 and parts[1]:
        try:
            last_updated = datetime.fromisoformat(parts[1]).isoformat()
        except ValueError:
            last_updated = None
    return status, last_updated


@bp.get("/network")
def network_status():
    try:
        raw_line = _STATUS_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return jsonify(
            {
                "status": "UNKNOWN",
                "raw": None,
                "last_updated": None,
                "error": f"Unable to read status file: {exc}",
            }
        )

    if not raw_line:
        return jsonify(
            {
                "status": "UNKNOWN",
                "raw": "",
                "last_updated": None,
                "error": "Status file is empty",
            }
        )

    status, last_updated = _parse_status_line(raw_line)
    return jsonify(
        {
            "status": status,
            "raw": raw_line,
            "last_updated": last_updated,
        }
    )
