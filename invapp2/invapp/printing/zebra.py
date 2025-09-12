"""Utilities for sending ZPL to Zebra printers."""

from flask import current_app
import socket
from typing import Optional, Tuple

from .labels import build_receiving_label
from urllib.request import Request, urlopen


def send_zpl(
    zpl: str,
    host: str = current_app.config["ZEBRA_PRINTER_HOST"],
    port: int = current_app.config["ZEBRA_PRINTER_PORT"],
) -> Tuple[bool, Optional[str]]:
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
    Tuple[bool, Optional[str]]
        A tuple of success flag and an error message. On success the
        message will be ``None``.
    """

    try:
        with socket.create_connection((host, port)) as sock:
            sock.sendall(zpl.encode("utf-8"))
        return True, None
    except socket.gaierror as exc:
        msg = (
            f"Unable to resolve printer host '{host}': {exc}. "
            "Verify the printer's hostname/IP in the application configuration."
        )
    except ConnectionRefusedError as exc:
        msg = (
            f"Connection to {host}:{port} was refused: {exc}. "
            "Ensure the printer is powered on and accepting connections on the configured port."
        )
    except TimeoutError as exc:
        msg = (
            f"Timed out while connecting to {host}:{port}: {exc}. "
            "Check network connectivity and printer settings."
        )
    except OSError as exc:
        msg = (
            f"Failed to send ZPL to printer at {host}:{port}: {exc}. "
            "Verify printer network configuration."
        )

    current_app.logger.error(msg)
    return False, msg


def print_receiving_label(sku: str, description: str, qty: int) -> Tuple[bool, Optional[str]]:
    """Generate and send a receiving label to the configured Zebra printer.

    Returns
    -------
    Tuple[bool, Optional[str]]
        ``True``/``False`` indicating if the label was printed and an
        optional error message.
    """

    zpl = build_receiving_label(sku, description, qty)
    return send_zpl(zpl)


def render_receiving_label_png(sku: str, description: str, qty: int) -> bytes:
    """Render a receiving label as a PNG using the Labelary API."""

    zpl = build_receiving_label(sku, description, qty)
    url = "http://api.labelary.com/v1/printers/8dpmm/labels/4x6/0/"
    request = Request(url, data=zpl.encode("utf-8"), headers={"Accept": "image/png"})
    with urlopen(request) as response:
        return response.read()

