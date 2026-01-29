from __future__ import annotations

from typing import Iterable

from flask import current_app

from invapp.extensions import db
from invapp.models import Printer, User


def list_available_printers() -> list[Printer]:
    return list(Printer.query.order_by(Printer.name.asc()).all())


def resolve_fallback_printer(printers: Iterable[Printer] | None = None) -> Printer | None:
    available = list(printers) if printers is not None else list_available_printers()
    if not available:
        return None

    configured_host = current_app.config.get("ZEBRA_PRINTER_HOST")
    configured_port = current_app.config.get("ZEBRA_PRINTER_PORT")
    if configured_host:
        candidate = (
            Printer.query.filter_by(host=configured_host, port=configured_port)
            .order_by(Printer.updated_at.desc())
            .first()
        )
        if candidate is not None:
            return candidate

    return available[0]


def get_user_default_printer(user: User | None) -> Printer | None:
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    printer_id = getattr(user, "default_printer_id", None)
    if not printer_id:
        return None

    printer = db.session.get(Printer, printer_id)
    if printer is not None:
        return printer

    current_app.logger.warning(
        "Default printer id '%s' for user %s no longer exists. Clearing selection.",
        printer_id,
        getattr(user, "username", "unknown"),
    )
    user.default_printer_id = None
    db.session.add(user)
    db.session.commit()
    return None


def set_user_default_printer(
    user: User,
    printer_identifier: int | str | None,
) -> Printer | None:
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    if printer_identifier is None or str(printer_identifier).strip() == "":
        previous = user.default_printer_id
        if previous:
            current_app.logger.info(
                "Default printer updated for %s: %s -> none.",
                getattr(user, "username", "unknown"),
                previous,
            )
        user.default_printer_id = None
        db.session.add(user)
        db.session.commit()
        return None

    printer = _resolve_printer_identifier(printer_identifier)
    if printer is None:
        raise ValueError("Selected printer does not exist.")

    previous = user.default_printer_id
    if previous != printer.id:
        current_app.logger.info(
            "Default printer updated for %s: %s -> %s.",
            getattr(user, "username", "unknown"),
            previous or "none",
            printer.id,
        )

    user.default_printer_id = printer.id
    db.session.add(user)
    db.session.commit()
    return printer


def resolve_user_printer(user: User | None) -> Printer | None:
    printer = get_user_default_printer(user)
    if printer is not None:
        return printer
    return resolve_fallback_printer()


def _resolve_printer_identifier(printer_identifier: int | str | None) -> Printer | None:
    if printer_identifier is None:
        return None
    try:
        printer_id = int(printer_identifier)
    except (TypeError, ValueError):
        printer_id = None

    if printer_id is not None:
        return db.session.get(Printer, printer_id)

    normalized = str(printer_identifier).strip()
    if not normalized:
        return None
    return Printer.query.filter_by(name=normalized).first()
