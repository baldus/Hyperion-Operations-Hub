"""Collector for backup status."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import time

from ..config import Config


def collect(config: Config) -> dict[str, Any]:
    status_path = config.backup_status_path
    if status_path.exists():
        payload = _read_json(status_path)
        if payload:
            return _status_from_payload(payload, inferred=False)

    fallback = _infer_from_directory(config.backup_dir)
    if fallback:
        return _status_from_payload(fallback, inferred=True)

    return {
        "ok": False,
        "status": "WARN",
        "details": "No backup status file or backups found.",
        "metrics": {},
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def _status_from_payload(payload: dict[str, Any], *, inferred: bool) -> dict[str, Any]:
    ok = bool(payload.get("ok"))
    status = "OK" if ok else "ERROR"
    started_at = payload.get("started_at")
    finished_at = payload.get("finished_at")
    filename = payload.get("filename")
    size_bytes = payload.get("size_bytes")
    error = payload.get("error")
    details = "Backup succeeded." if ok else "Backup failed."
    if inferred:
        details += " (inferred)"
    if error:
        details += f" Error: {error}"

    age_minutes = _age_minutes(finished_at or started_at)
    metrics = {
        "started_at": started_at,
        "finished_at": finished_at,
        "filename": filename,
        "size_bytes": size_bytes,
        "duration_sec": payload.get("duration_sec"),
        "age_minutes": age_minutes,
        "inferred": inferred,
    }
    return {
        "ok": ok,
        "status": status,
        "details": details,
        "metrics": metrics,
    }


def _age_minutes(timestamp: str | None) -> float | None:
    if not timestamp:
        return None
    try:
        dt = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    return round(delta.total_seconds() / 60, 2)


def _infer_from_directory(directory: Path) -> dict[str, Any] | None:
    if not directory.exists():
        return None
    latest: tuple[Path, float] | None = None
    for entry in directory.rglob("*"):
        if not entry.is_file():
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if latest is None or mtime > latest[1]:
            latest = (entry, mtime)
    if not latest:
        return None
    path, mtime = latest
    finished_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    return {
        "ok": True,
        "started_at": finished_at,
        "finished_at": finished_at,
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "duration_sec": None,
        "error": None,
    }
