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

    printer_name = (user.default_printer or "").strip()
    if not printer_name:
        return None

    printer = Printer.query.filter_by(name=printer_name).first()
    if printer is not None:
        return printer

    current_app.logger.warning(
        "Default printer '%s' for user %s no longer exists. Clearing selection.",
        printer_name,
        getattr(user, "username", "unknown"),
    )
    user.default_printer = None
    db.session.add(user)
    db.session.commit()
    return None


def set_user_default_printer(user: User, printer_name: str | None) -> Printer | None:
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    normalized = (printer_name or "").strip()
    if not normalized:
        previous = user.default_printer
        if previous:
            current_app.logger.info(
                "Default printer updated for %s: %s -> none.",
                getattr(user, "username", "unknown"),
                previous,
            )
        user.default_printer = None
        db.session.add(user)
        db.session.commit()
        return None

    printer = Printer.query.filter_by(name=normalized).first()
    if printer is None:
        raise ValueError("Selected printer does not exist.")

    previous = user.default_printer
    if previous != normalized:
        current_app.logger.info(
            "Default printer updated for %s: %s -> %s.",
            getattr(user, "username", "unknown"),
            previous or "none",
            normalized,
        )

    user.default_printer = normalized
    db.session.add(user)
    db.session.commit()
    return printer


def resolve_user_printer(user: User | None) -> Printer | None:
    printer = get_user_default_printer(user)
    if printer is not None:
        return printer
    return resolve_fallback_printer()
