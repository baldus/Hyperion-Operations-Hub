"""Utilities for sending ZPL to Zebra printers."""

from __future__ import annotations

from typing import Mapping

import socket
from urllib.request import Request, urlopen

from flask import current_app

from .labels import build_receiving_label, render_label_for_process


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


def print_label_for_process(process: str, context: Mapping[str, object]) -> bool:
    """Render and send the label configured for a specific process."""

    zpl = render_label_for_process(process, context)
    return send_zpl(zpl)


def render_receiving_label_png(sku: str, description: str, qty: int) -> bytes:
    """Render a receiving label as a PNG using the Labelary API."""

    return render_label_png_for_process(
        "BatchCreated",
        {
            "Batch": {
                "Quantity": qty,
                "Item": {"SKU": sku, "Description": description},
            }
        },
    )


def render_label_png_for_process(
    process: str,
    context: Mapping[str, object],
    *,
    dpi: str = "8dpmm",
    size: str = "4x6",
    index: int = 0,
) -> bytes:
    """Render the configured process label as PNG using the Labelary API."""

    zpl = render_label_for_process(process, context)
    url = f"http://api.labelary.com/v1/printers/{dpi}/labels/{size}/{index}/"
    request = Request(url, data=zpl.encode("utf-8"), headers={"Accept": "image/png"})
    with urlopen(request) as response:
        return response.read()

