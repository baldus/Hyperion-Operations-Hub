"""Shared printing service for label rendering + Zebra delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from flask import current_app

from invapp.printing.labels import (
    build_item_label_context,
    build_transfer_label_context,
    render_label_for_process,
)
from invapp.printing.zebra import send_zpl
from invapp.services import status_bus


LABEL_PROCESS_MAP = {
    "item": "ItemLabel",
    "transfer": "InventoryTransferLabel",
}


@dataclass(frozen=True)
class PrintResult:
    ok: bool
    label_type: str
    message: str
    zpl: str | None = None
    error: str | None = None


def _printer_configured() -> tuple[bool, str | None]:
    host = current_app.config.get("ZEBRA_PRINTER_HOST")
    port = current_app.config.get("ZEBRA_PRINTER_PORT")
    if not host or port in (None, ""):
        return False, "Printer is not configured."
    return True, None


def print_label(label_type: str, context: Mapping[str, Any], *, copies: int = 1) -> PrintResult:
    process = LABEL_PROCESS_MAP.get(label_type)
    if not process:
        message = f"Unknown label type '{label_type}'."
        current_app.logger.error(message)
        return PrintResult(False, label_type, message, error=message)

    try:
        zpl = render_label_for_process(process, context)
    except KeyError as exc:
        message = f"No label template configured for {label_type} labels."
        current_app.logger.error("Label rendering failed: %s", exc)
        status_bus.log_event(
            "error",
            message,
            source="printing",
            context={"label_type": label_type, "process": process},
        )
        return PrintResult(False, label_type, message, error=message)

    if current_app.config.get("PRINT_DRY_RUN"):
        return PrintResult(
            True,
            label_type,
            "Dry run enabled; label generated but not sent.",
            zpl=zpl,
        )

    configured, config_error = _printer_configured()
    if not configured:
        status_bus.log_event(
            "error",
            config_error or "Printer is not configured.",
            source="printing",
            context={"label_type": label_type, "process": process},
        )
        return PrintResult(False, label_type, config_error or "Printer is not configured.", error=config_error)

    copies = max(int(copies or 1), 1)
    for _ in range(copies):
        if not send_zpl(zpl):
            error_message = "Failed to send label to printer."
            status_bus.log_event(
                "error",
                error_message,
                source="printing",
                context={"label_type": label_type, "process": process},
            )
            return PrintResult(False, label_type, error_message, zpl=zpl, error=error_message)

    return PrintResult(True, label_type, "Label sent to printer.", zpl=zpl)


def print_item_label(item: Any, *, copies: int = 1) -> PrintResult:
    context = build_item_label_context(item)
    return print_label("item", context, copies=copies)


def print_transfer_label(
    *,
    item: Any,
    quantity: Any,
    batch: Any | None = None,
    from_location: Any | None = None,
    to_location: Any | None = None,
    reference: str | None = None,
    person: str | None = None,
    moved_at: Any | None = None,
    copies: int = 1,
) -> PrintResult:
    context = build_transfer_label_context(
        item,
        quantity=quantity,
        batch=batch,
        from_location=from_location,
        to_location=to_location,
        reference=reference,
        person=person,
        moved_at=moved_at,
    )
    return print_label("transfer", context, copies=copies)

