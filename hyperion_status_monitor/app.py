"""Flask entrypoint for Hyperion Status Monitor."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading
import time
from typing import Any

from flask import Flask, jsonify, render_template

from .config import Config
from .snapshot import build_snapshot
from .store import StatusStore


SNAPSHOT_STALE_MULTIPLIER = 3
EVENT_LIMIT = 25


class SnapshotState:
    def __init__(self) -> None:
        self.snapshot: dict[str, Any] | None = None
        self.generated_at: str | None = None
        self.lock = threading.Lock()

    def update(self, snapshot: dict[str, Any], generated_at: str) -> None:
        with self.lock:
            self.snapshot = snapshot
            self.generated_at = generated_at

    def get(self) -> dict[str, Any] | None:
        with self.lock:
            if not self.snapshot:
                return None
            return dict(self.snapshot)


class EventBuffer:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.lock = threading.Lock()
        self.events: list[dict[str, Any]] = []

    def add(self, event: dict[str, Any]) -> None:
        with self.lock:
            self.events.insert(0, event)
            self.events = self.events[: self.limit]

    def all(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.events)


def create_app() -> Flask:
    config = Config.from_env()
    _configure_logging(config)

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    store = StatusStore(config.db_path)
    state = SnapshotState()
    event_buffer = EventBuffer(EVENT_LIMIT)
    start_time = time.monotonic()

    try:
        store.initialize()
    except Exception as exc:  # noqa: BLE001 - keep service alive
        app.logger.exception("Failed to initialize store: %s", exc)

    latest = None
    try:
        latest = store.load_latest_snapshot()
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("Failed to load latest snapshot: %s", exc)

    if latest:
        state.update(latest.snapshot_json, latest.generated_at)

    stop_event = threading.Event()

    def record_error(level: str, message: str, context: dict[str, Any]) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        event = {
            "timestamp": timestamp,
            "level": level,
            "message": message,
            "context": context,
        }
        event_buffer.add(event)
        try:
            store.record_event(timestamp, level, message, context)
        except Exception as exc:  # noqa: BLE001
            app.logger.exception("Failed to record event: %s", exc)

    def collect_loop() -> None:
        while not stop_event.is_set():
            result = build_snapshot(
                config,
                start_time,
                app.logger,
                record_error,
                config.collector_timeout_sec,
            )
            try:
                store.save_snapshot(result.generated_at, result.snapshot)
            except Exception as exc:  # noqa: BLE001
                app.logger.exception("Failed to persist snapshot: %s", exc)
                record_error(
                    "error",
                    "Failed to persist snapshot.",
                    {"error": str(exc)},
                )
            try:
                events = store.load_events(limit=EVENT_LIMIT)
            except Exception as exc:  # noqa: BLE001
                app.logger.exception("Failed to load events: %s", exc)
                events = []
            result.snapshot["errors"] = [
                {
                    "timestamp": event.timestamp,
                    "level": event.level,
                    "message": event.message,
                    "context": event.context,
                }
                for event in events
            ]
            state.update(result.snapshot, result.generated_at)
            stop_event.wait(config.interval_sec)

    thread = threading.Thread(target=collect_loop, name="snapshot-collector", daemon=True)
    thread.start()

    @app.route("/")
    def index() -> str:
        return render_template("index.html", interval=config.interval_sec)

    @app.route("/api/status/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.route("/api/status/snapshot")
    def snapshot() -> Any:
        snapshot_data = state.get()
        if snapshot_data:
            return jsonify(snapshot_data)
        try:
            latest_snapshot = store.load_latest_snapshot()
        except Exception as exc:  # noqa: BLE001
            app.logger.exception("Failed to load snapshot fallback: %s", exc)
            latest_snapshot = None
        if latest_snapshot:
            return jsonify(latest_snapshot.snapshot_json)
        return jsonify({"generated_at": None, "errors": []})

    @app.route("/api/status/events")
    def events() -> Any:
        try:
            events_list = store.load_events(limit=EVENT_LIMIT)
        except Exception as exc:  # noqa: BLE001
            app.logger.exception("Failed to load events: %s", exc)
            events_list = []
        return jsonify(
            [
                {
                    "timestamp": event.timestamp,
                    "level": event.level,
                    "message": event.message,
                    "context": event.context,
                }
                for event in events_list
            ]
        )

    @app.route("/api/status/diagnostics")
    def diagnostics() -> Any:
        snapshot_data = state.get() or {}
        errors = snapshot_data.get("errors") or []
        diagnostics_text = _build_diagnostics(
            config,
            start_time,
            snapshot_data,
            errors,
            event_buffer,
        )
        return app.response_class(diagnostics_text, mimetype="text/plain")

    @app.after_request
    def add_cache_headers(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


def _build_diagnostics(
    config: Config,
    start_time: float,
    snapshot_data: dict[str, Any],
    errors: list[dict[str, Any]],
    event_buffer: EventBuffer,
) -> str:
    uptime_sec = max(0.0, time.monotonic() - start_time)
    lines = [
        "Hyperion Status Monitor Diagnostics",
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"Uptime (sec): {uptime_sec:.2f}",
        f"Port: {config.port}",
        f"DB Path: {config.db_path}",
        f"Log Path: {config.log_path}",
        f"Backup Status Path: {config.backup_status_path}",
        f"Backup Dir: {config.backup_dir}",
        f"Main App Health URL: {config.main_app_health_url or 'not configured'}",
        "",
        "Last Snapshot Summary:",
        json.dumps(snapshot_data, indent=2, sort_keys=True),
        "",
        "Recent Errors:",
    ]
    for error in errors[:10]:
        lines.append(
            f"- {error.get('timestamp')} [{error.get('level')}] {error.get('message')}"
        )
    if not errors:
        lines.append("- none")
    lines.append("")
    lines.append("Recent Events (buffered):")
    for event in event_buffer.all():
        lines.append(
            f"- {event.get('timestamp')} [{event.get('level')}] {event.get('message')}"
        )
    if not event_buffer.all():
        lines.append("- none")
    return "\n".join(lines)


def _configure_logging(config: Config) -> None:
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    log_dir = config.log_path.parent
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    file_handler = RotatingFileHandler(
        config.log_path, maxBytes=2 * 1024 * 1024, backupCount=5
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)


if __name__ == "__main__":
    app = create_app()
    config = Config.from_env()
    app.run(host=config.host, port=config.port)
