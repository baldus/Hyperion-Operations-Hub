"""Form parsing helpers for physical inventory workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from werkzeug.datastructures import FileStorage


@dataclass
class SnapshotUploadPayload:
    name: str | None
    source: str | None
    snapshot_date: datetime | None
    file: FileStorage


@dataclass
class CountUpdate:
    line_id: int
    counted_qty: str | None
    notes: str | None


def parse_snapshot_upload_form(
    form: Mapping[str, str],
    files: Mapping[str, FileStorage],
) -> tuple[SnapshotUploadPayload | None, list[str]]:
    errors: list[str] = []
    name = (form.get("name") or "").strip() or None
    source = (form.get("source") or "").strip() or None
    raw_date = (form.get("snapshot_date") or "").strip()
    snapshot_date = None

    if raw_date:
        try:
            snapshot_date = datetime.strptime(raw_date, "%Y-%m-%d")
        except ValueError:
            errors.append("Snapshot date must be in YYYY-MM-DD format.")

    upload = files.get("snapshot_csv")
    if upload is None or not upload.filename:
        errors.append("Please select a CSV file to upload.")

    if errors:
        return None, errors

    return SnapshotUploadPayload(
        name=name,
        source=source,
        snapshot_date=snapshot_date,
        file=upload,
    ), []


def parse_count_updates(form: Mapping[str, str]) -> list[CountUpdate]:
    updates: list[CountUpdate] = []
    for raw_id in form.getlist("line_id"):
        try:
            line_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        counted_qty = (form.get(f"counted_qty_{line_id}") or "").strip() or None
        notes = (form.get(f"notes_{line_id}") or "").strip() or None
        updates.append(CountUpdate(line_id=line_id, counted_qty=counted_qty, notes=notes))
    return updates
