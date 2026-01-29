"""Printer registry + resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from flask import current_app
from sqlalchemy import inspect
from sqlalchemy.orm.exc import DetachedInstanceError

from invapp.extensions import db
from invapp.models import Printer, User
from invapp.services import status_bus


@dataclass(frozen=True)
class PrinterTarget:
    id: int | None
    name: str | None
    host: str | None
    port: int | None
    kind: str | None
    source: str

    def as_dict(self) -> dict[str, object | None]:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "kind": self.kind,
            "source": self.source,
        }


@dataclass(frozen=True)
class PrintResult:
    ok: bool
    label_type: str
    message: str
    zpl: str | None = None
    error: str | None = None
    warnings: tuple[str, ...] = ()
    printer: PrinterTarget | None = None


@dataclass(frozen=True)
class PrinterResolution:
    target: PrinterTarget | None
    warnings: tuple[str, ...]


def list_available_printers() -> list[Printer]:
    return list(Printer.query.filter_by(enabled=True).order_by(Printer.name.asc()).all())


def printer_configured(target: PrinterTarget | None) -> tuple[bool, str | None]:
    host = target.host if target else current_app.config.get("ZEBRA_PRINTER_HOST")
    port = target.port if target else current_app.config.get("ZEBRA_PRINTER_PORT")
    if not host or port in (None, ""):
        return False, "Printer is not configured."
    return True, None


def _coerce_printer(value: Printer | int | str | None) -> Printer | None:
    if value is None:
        return None
    if isinstance(value, Printer):
        return value
    try:
        printer_id = int(value)
    except (TypeError, ValueError):
        return None
    return Printer.query.get(printer_id)


def _build_target(printer: Printer, source: str) -> PrinterTarget:
    port = printer.port
    if port is None:
        port = current_app.config.get("ZEBRA_PRINTER_PORT")
    return PrinterTarget(
        id=printer.id,
        name=printer.name,
        host=printer.host,
        port=port,
        kind=printer.printer_type,
        source=source,
    )


def system_default_printer_target() -> PrinterTarget | None:
    host = current_app.config.get("ZEBRA_PRINTER_HOST")
    port = current_app.config.get("ZEBRA_PRINTER_PORT")
    if not host or port in (None, ""):
        return None
    return PrinterTarget(
        id=None,
        name="System default",
        host=host,
        port=int(port) if port is not None else None,
        kind="zebra",
        source="system_default",
    )


def get_user_default_printer(user: User | None) -> PrinterResolution:
    if user is None or not getattr(user, "is_authenticated", False):
        return PrinterResolution(None, ())
    try:
        default_id = getattr(user, "default_printer_id", None)
    except DetachedInstanceError:
        identity = inspect(user).identity
        default_id = None
        if identity:
            refreshed = db.session.get(User, identity[0])
            default_id = getattr(refreshed, "default_printer_id", None) if refreshed else None
    if not default_id:
        return PrinterResolution(None, ())

    printer = Printer.query.get(default_id)
    if printer is None:
        message = "Default printer is no longer available. Using system default."
        status_bus.log_event(
            "warning",
            message,
            source="printing",
            context={"user_id": user.id, "printer_id": default_id},
            dedupe_key=f"printer:missing:{user.id}:{default_id}",
        )
        return PrinterResolution(None, (message,))
    if not printer.enabled:
        message = "Default printer is disabled. Using system default."
        status_bus.log_event(
            "warning",
            message,
            source="printing",
            context={"user_id": user.id, "printer_id": printer.id},
            dedupe_key=f"printer:disabled:{user.id}:{printer.id}",
        )
        return PrinterResolution(None, (message,))

    return PrinterResolution(_build_target(printer, "user_default"), ())


def resolve_effective_printer(
    *,
    user: User | None,
    override: Printer | int | str | None = None,
) -> PrinterResolution:
    warnings: list[str] = []

    if override is not None:
        printer = _coerce_printer(override)
        if printer is None or not printer.enabled:
            message = "Selected printer is unavailable. Using system default."
            status_bus.log_event(
                "warning",
                message,
                source="printing",
                context={"printer_id": getattr(printer, "id", override)},
            )
            warnings.append(message)
            return PrinterResolution(system_default_printer_target(), tuple(warnings))
        return PrinterResolution(_build_target(printer, "override"), tuple(warnings))

    user_resolution = get_user_default_printer(user)
    if user_resolution.target is not None:
        return user_resolution
    warnings.extend(user_resolution.warnings)

    return PrinterResolution(system_default_printer_target(), tuple(warnings))


def fallback_to_system_default(
    target: PrinterTarget | None,
) -> PrinterTarget | None:
    if target is None:
        return None
    fallback = system_default_printer_target()
    if fallback is None:
        return None
    if (fallback.host, fallback.port) == (target.host, target.port):
        return None
    return fallback


def ensure_printer_enabled(printer_id: int | None) -> Printer | None:
    if printer_id is None:
        return None
    printer = Printer.query.get(printer_id)
    if printer is None or not printer.enabled:
        return None
    return printer


def printer_choices() -> Iterable[tuple[str, str]]:
    yield ("", "None (use system default)")
    for printer in list_available_printers():
        label = printer.name
        if printer.location:
            label = f"{label} — {printer.location}"
        yield (str(printer.id), label)
