"""Utilities for sending ZPL to Zebra printers."""

from flask import current_app
import socket

from .labels import build_receiving_label


def send_zpl(
    zpl: str,
    host: str = current_app.config["ZEBRA_PRINTER_HOST"],
    port: int = current_app.config["ZEBRA_PRINTER_PORT"],
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

    try:
        with socket.create_connection((host, port)) as sock:
            sock.sendall(zpl.encode("utf-8"))
        return True
    except OSError as exc:
        current_app.logger.error("Failed to send ZPL to printer: %s", exc)
        return False


def print_receiving_label(sku: str, description: str, qty: int) -> bool:
    """Generate and send a receiving label to the configured Zebra printer."""

    zpl = build_receiving_label(sku, description, qty)
    return send_zpl(zpl)

