"""Centralized status/event bus for the operations monitor."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from typing import Any, Deque

from sqlalchemy.exc import SQLAlchemyError

from invapp.extensions import db
from invapp.models import OpsEventLog


_EVENTS: Deque[dict[str, Any]] = deque(maxlen=200)
_DEDUPE: dict[str, dict[str, Any]] = {}


def log_event(
    level: str,
    message: str,
    *,
    context: dict[str, Any] | None = None,
    source: str | None = None,
    dedupe_key: str | None = None,
) -> None:
    timestamp = datetime.utcnow()
    normalized_level = level.upper()

    if dedupe_key and dedupe_key in _DEDUPE:
        event = _DEDUPE[dedupe_key]
        event["count"] += 1
        event["timestamp"] = timestamp
        event["context"] = context or event.get("context")
        return

    event = {
        "timestamp": timestamp,
        "level": normalized_level,
        "message": message,
        "context": context,
        "source": source,
        "count": 1,
    }
    _EVENTS.append(event)
    if dedupe_key:
        _DEDUPE[dedupe_key] = event

    try:
        record = OpsEventLog(
            level=normalized_level,
            source=source,
            message=message,
            context_json=context,
        )
        db.session.add(record)
        db.session.commit()
    except (SQLAlchemyError, RuntimeError):
        db.session.rollback()


def get_recent_events(limit: int = 200) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    return list(_EVENTS)[-limit:]


class StatusBusHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            message = record.msg if isinstance(record.msg, str) else "log message"

        log_event(
            record.levelname,
            message,
            source=record.name,
            dedupe_key=f"log:{record.levelname}:{message}",
        )
