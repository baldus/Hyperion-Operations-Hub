"""SQLite persistence for snapshots and events."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class StoredSnapshot:
    generated_at: str
    snapshot_json: dict[str, Any]


@dataclass
class StoredEvent:
    timestamp: str
    level: str
    message: str
    context: dict[str, Any]


class StatusStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generated_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    context_json TEXT NOT NULL
                )
                """
            )

    def save_snapshot(self, generated_at: str, snapshot: dict[str, Any]) -> None:
        payload = json.dumps(snapshot, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO snapshots (generated_at, snapshot_json) VALUES (?, ?)",
                (generated_at, payload),
            )

    def record_event(
        self, timestamp: str, level: str, message: str, context: dict[str, Any]
    ) -> None:
        payload = json.dumps(context, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events (timestamp, level, message, context_json) VALUES (?, ?, ?, ?)",
                (timestamp, level, message, payload),
            )

    def load_latest_snapshot(self) -> StoredSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT generated_at, snapshot_json FROM snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return StoredSnapshot(generated_at=row[0], snapshot_json=json.loads(row[1]))

    def load_events(self, limit: int = 25) -> list[StoredEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT timestamp, level, message, context_json "
                "FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            StoredEvent(
                timestamp=row[0],
                level=row[1],
                message=row[2],
                context=json.loads(row[3]),
            )
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)
