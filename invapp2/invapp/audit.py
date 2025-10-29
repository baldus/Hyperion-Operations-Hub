"""Utilities for recording access and authentication activity."""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping

from flask import current_app, request
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from invapp.extensions import db
from invapp import models


def _trimmed(value: str | None, *, limit: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return value[:limit]


def resolve_client_ip() -> str | None:
    """Best effort extraction of the originating client IP address."""

    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        if parts:
            return parts[0][:64]

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()[:64]

    remote_addr = request.remote_addr
    if remote_addr:
        return str(remote_addr)[:64]

    return None


def _sessionmaker():
    """Return a session factory bound to the active engine."""

    return sessionmaker(bind=db.engine, future=True)


def _resolve_engine(bind: Connection | Engine | None) -> Engine | None:
    if bind is None:
        return getattr(db, "engine", None)

    if isinstance(bind, Engine):
        return bind

    if isinstance(bind, Connection):
        return bind.engine

    return getattr(db, "engine", None)


def _is_duplicate_primary_key(error: IntegrityError) -> bool:
    if not isinstance(error, IntegrityError):
        return False

    original = getattr(error, "orig", None)
    pgcode = getattr(original, "pgcode", None)
    if pgcode == "23505":
        diag = getattr(original, "diag", None)
        constraint = getattr(diag, "constraint_name", None) if diag else None
        if constraint:
            return constraint == "access_log_pkey"

    return "access_log_pkey" in str(error).lower()


def _repair_access_log_sequence(bind: Connection | Engine | None) -> bool:
    engine = _resolve_engine(bind)
    if engine is None:
        return False

    dialect = engine.dialect.name

    try:
        with engine.begin() as connection:
            max_identifier = connection.execute(
                text("SELECT COALESCE(MAX(id), 0) FROM access_log")
            ).scalar()

            if dialect == "postgresql":
                sequence_sql = text(
                    "SELECT setval("
                    "pg_get_serial_sequence(:table_name, 'id'), "
                    ":value, :is_called)"
                )
                connection.execute(
                    sequence_sql,
                    {
                        "table_name": "access_log",
                        "value": max_identifier if max_identifier else 1,
                        "is_called": bool(max_identifier),
                    },
                )
                return True

            if dialect == "sqlite":
                has_sequence = connection.execute(
                    text(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'sqlite_sequence'"
                    )
                ).fetchone()
                if not has_sequence:
                    return False

                connection.execute(
                    text(
                        "UPDATE sqlite_sequence "
                        "SET seq = :value "
                        "WHERE name = :table_name"
                    ),
                    {
                        "table_name": "access_log",
                        "value": max_identifier,
                    },
                )
                return True
    except Exception:
        current_app.logger.exception("Failed to repair access log sequence")
        return False

    return False


def record_access_event(
    *,
    event_type: str,
    user_id: int | None = None,
    username: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    method: str | None = None,
    path: str | None = None,
    endpoint: str | None = None,
    status_code: int | None = None,
    details: Mapping[str, Any] | None = None,
) -> None:
    """Persist an :class:`~invapp.models.AccessLog` entry safely."""

    try:
        Session = _sessionmaker()
    except RuntimeError:
        # Outside of an application context the engine is unavailable.
        return

    payload: MutableMapping[str, Any] = {
        "event_type": event_type,
        "user_id": user_id,
        "username": _trimmed(username, limit=255),
        "ip_address": _trimmed(ip_address, limit=64),
        "user_agent": _trimmed(user_agent, limit=512),
        "method": _trimmed(method, limit=16),
        "path": _trimmed(path, limit=512),
        "endpoint": _trimmed(endpoint, limit=255),
        "status_code": status_code,
    }

    if details:
        payload["details"] = dict(details)

    session = Session()
    try:
        session.add(models.AccessLog(**payload))
        session.commit()
    except IntegrityError as error:
        session.rollback()
        if _is_duplicate_primary_key(error) and _repair_access_log_sequence(
            session.get_bind()
        ):
            session.add(models.AccessLog(**payload))
            try:
                session.commit()
                return
            except SQLAlchemyError:
                session.rollback()
        current_app.logger.exception("Failed to record access log entry")
    except SQLAlchemyError:
        session.rollback()
        current_app.logger.exception("Failed to record access log entry")
    finally:
        session.close()


def record_login_event(
    *,
    event_type: str,
    user_id: int | None,
    username: str | None,
    status_code: int,
) -> None:
    """Helper for logging authentication outcomes."""

    record_access_event(
        event_type=event_type,
        user_id=user_id,
        username=username,
        ip_address=resolve_client_ip(),
        user_agent=request.user_agent.string if request.user_agent else None,
        method=request.method,
        path=request.path,
        endpoint=request.endpoint,
        status_code=status_code,
    )
