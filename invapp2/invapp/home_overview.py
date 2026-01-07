from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from invapp.models import PurchaseRequest
from invapp.mdi.materials_summary import extract_sku_from_title


@dataclass(frozen=True)
class IncomingItemSummary:
    id: int
    item_number: str | None
    title: str
    description: str | None
    supplier: str | None
    ordered_display: str
    received_display: str
    eta_date: date


def _format_quantity(value: Decimal | None, unit: str | None) -> str:
    if value is None:
        return "—"
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if not text:
        text = "0"
    if unit:
        return f"{text} {unit}"
    return text


def _build_summary(request: PurchaseRequest) -> IncomingItemSummary:
    ordered_display = _format_quantity(request.quantity, request.unit)
    received_display = _format_quantity(Decimal("0"), request.unit)
    return IncomingItemSummary(
        id=request.id,
        item_number=extract_sku_from_title(request.title),
        title=request.title,
        description=request.description,
        supplier=request.supplier_name,
        ordered_display=ordered_display,
        received_display=received_display,
        eta_date=request.eta_date,
    )


def get_incoming_and_overdue_items(
    *,
    today: date | None = None,
    window_days: int = 3,
) -> tuple[list[IncomingItemSummary], list[IncomingItemSummary]]:
    current_day = today or date.today()
    window_end = current_day + timedelta(days=window_days)
    open_statuses = set(PurchaseRequest.status_values()) - {
        PurchaseRequest.STATUS_RECEIVED,
        PurchaseRequest.STATUS_CANCELLED,
    }

    base_query = PurchaseRequest.query.filter(
        PurchaseRequest.status.in_(open_statuses),
        PurchaseRequest.eta_date.isnot(None),
    )

    overdue_items = (
        base_query.filter(PurchaseRequest.eta_date < current_day)
        .order_by(PurchaseRequest.eta_date.asc(), PurchaseRequest.id.asc())
        .all()
    )
    incoming_items = (
        base_query.filter(
            PurchaseRequest.eta_date >= current_day,
            PurchaseRequest.eta_date <= window_end,
        )
        .order_by(PurchaseRequest.eta_date.asc(), PurchaseRequest.id.asc())
        .all()
    )

    return (
        [_build_summary(item) for item in overdue_items],
        [_build_summary(item) for item in incoming_items],
    )
