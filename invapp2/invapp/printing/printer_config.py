"""Utilities for resolving the active printer configuration."""

from __future__ import annotations

from flask import current_app, has_app_context, has_request_context, session


def ensure_printer_configuration() -> None:
    """Ensure ``current_app`` is configured with an active printer host/port.

    When the application starts up the Zebra printer host/port may not yet be
    configured even though printers are stored in the database. This helper
    resolves the active printer using, in order of precedence:

    1. The printer selected in the current user's session.
    2. The most recently updated printer record.

    If a printer is found the Flask configuration is updated so subsequent
    printing calls use the correct connection details.
    """

    if not has_app_context():  # pragma: no cover - safety guard for CLI usage
        return

    host = (current_app.config.get("ZEBRA_PRINTER_HOST") or "").strip()
    port = current_app.config.get("ZEBRA_PRINTER_PORT")

    # If an explicit host has already been configured we do not override it.
    if host and host != "localhost":
        return

    try:
        from invapp.models import Printer
    except ImportError:  # pragma: no cover - defensive guard during bootstrap
        return

    printer = None

    if has_request_context():
        selected_id = session.get("selected_printer_id")
        if selected_id:
            printer = Printer.query.get(selected_id)

    if printer is None:
        printer = Printer.query.order_by(Printer.updated_at.desc()).first()

    if printer is None:
        return

    current_app.config["ZEBRA_PRINTER_HOST"] = printer.host
    if printer.port is not None:
        current_app.config["ZEBRA_PRINTER_PORT"] = printer.port
    elif port is None:
        current_app.config.setdefault("ZEBRA_PRINTER_PORT", 9100)

