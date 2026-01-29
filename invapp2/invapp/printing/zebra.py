"""Utilities for sending ZPL to Zebra printers."""

from collections.abc import Mapping
import socket
from urllib.request import Request, urlopen

from flask import current_app

from .labels import build_receiving_label, render_label_for_process
from .printers import (
    PrintResult,
    fallback_to_system_default,
    printer_configured,
    resolve_effective_printer,
)
from invapp.services import status_bus


def send_zpl(
    zpl: str,
    host: str | None = None,
    port: int | None = None,
) -> bool:
    """Send raw ZPL to a networked Zebra printer.

    Parameters
    ----------
    zpl:
        Raw ZPL string to send to the printer.
    host:
        Printer hostname or IP address.
    port:
        TCP port to connect to on the printer.

    Returns
    -------
    bool
        ``True`` if the data was sent successfully, ``False`` otherwise.
    """

    resolved_host = host or current_app.config["ZEBRA_PRINTER_HOST"]
    resolved_port = port or current_app.config["ZEBRA_PRINTER_PORT"]

    try:
        with socket.create_connection((resolved_host, resolved_port)) as sock:
            sock.sendall(zpl.encode("utf-8"))
        return True
    except OSError as exc:
        current_app.logger.error("Failed to send ZPL to printer: %s", exc)
        return False


def print_receiving_label(
    batch_or_sku,
    description: str | None = None,
    qty: int | None = None,
    *,
    item: object | None = None,
    location: object | None = None,
    po_number: str | None = None,
    lot_number: str | None = None,
    user: object | None = None,
    override_printer: object | None = None,
) -> PrintResult:
    """Generate and send a receiving (batch) label to the configured Zebra printer."""

    zpl = build_receiving_label(
        batch_or_sku,
        description,
        qty,
        item=item,
        location=location,
        po_number=po_number,
        lot_number=lot_number,
    )
    return print_zpl(
        zpl,
        label_type="receiving",
        user=user,
        override_printer=override_printer,
    )


def print_label_for_process(
    process: str,
    context: Mapping[str, object],
    *,
    user: object | None = None,
    override_printer: object | None = None,
) -> PrintResult:
    """Render the label assigned to ``process`` and send it to the printer."""

    try:
        zpl = render_label_for_process(process, context)
    except KeyError as exc:  # pragma: no cover - defensive logging
        current_app.logger.error("No label template for process '%s': %s", process, exc)
        return PrintResult(False, process, "Label template is not configured.", error=str(exc))
    return print_zpl(
        zpl,
        label_type=process,
        user=user,
        override_printer=override_printer,
    )


def print_zpl(
    zpl: str,
    *,
    label_type: str,
    user: object | None = None,
    override_printer: object | None = None,
) -> PrintResult:
    resolution = resolve_effective_printer(user=user, override=override_printer)
    warnings = resolution.warnings

    if current_app.config.get("PRINT_DRY_RUN"):
        return PrintResult(
            True,
            label_type,
            "Dry run enabled; label generated but not sent.",
            zpl=zpl,
            warnings=warnings,
            printer=resolution.target,
        )

    configured, config_error = printer_configured(resolution.target)
    if not configured:
        status_bus.log_event(
            "error",
            config_error or "Printer is not configured.",
            source="printing",
            context={"label_type": label_type},
        )
        return PrintResult(
            False,
            label_type,
            config_error or "Printer is not configured.",
            error=config_error,
            warnings=warnings,
            printer=resolution.target,
        )

    ok = send_zpl(
        zpl,
        host=resolution.target.host if resolution.target else None,
        port=resolution.target.port if resolution.target else None,
    )
    if not ok:
        fallback_target = None
        if resolution.target and resolution.target.source == "user_default":
            fallback_target = fallback_to_system_default(resolution.target)
        if fallback_target and send_zpl(
            zpl,
            host=fallback_target.host,
            port=fallback_target.port,
        ):
            warning_message = "Default printer unreachable. Sent to system default."
            status_bus.log_event(
                "warning",
                warning_message,
                source="printing",
                context={"label_type": label_type},
            )
            warnings = tuple((*warnings, warning_message))
            return PrintResult(
                True,
                label_type,
                "Label sent to printer.",
                zpl=zpl,
                warnings=warnings,
                printer=fallback_target,
            )

        error_message = "Failed to send label to printer."
        status_bus.log_event(
            "error",
            error_message,
            source="printing",
            context={"label_type": label_type},
        )
        return PrintResult(
            False,
            label_type,
            error_message,
            zpl=zpl,
            error=error_message,
            warnings=warnings,
            printer=resolution.target,
        )

    return PrintResult(
        True,
        label_type,
        "Label sent to printer.",
        zpl=zpl,
        warnings=warnings,
        printer=resolution.target,
    )


def render_receiving_label_png(
    batch_or_sku,
    description: str | None = None,
    qty: int | None = None,
    **kwargs,
) -> bytes:
    """Render a receiving label as a PNG using the Labelary API."""

    zpl = build_receiving_label(batch_or_sku, description, qty, **kwargs)
    url = "http://api.labelary.com/v1/printers/8dpmm/labels/4x6/0/"
    request = Request(url, data=zpl.encode("utf-8"), headers={"Accept": "image/png"})
    with urlopen(request) as response:
        return response.read()
