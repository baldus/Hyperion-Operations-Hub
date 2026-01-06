from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from typing import Iterable, List

from sqlalchemy import func

from invapp.extensions import db
from invapp.models import PurchaseRequest


STATUS_PRIORITY = [
    "New",
    "Waiting on Supplier",
    "Ordered",
]


def status_display_label(status: str | None) -> str:
    if not status:
        return "Other"
    mapping = {
        PurchaseRequest.STATUS_NEW: "New",
        PurchaseRequest.STATUS_REVIEW: "Reviewing",
        PurchaseRequest.STATUS_WAITING: "Waiting on Supplier",
        PurchaseRequest.STATUS_ORDERED: "Ordered",
        PurchaseRequest.STATUS_RECEIVED: "Received",
        PurchaseRequest.STATUS_CANCELLED: "Cancelled",
    }
    return mapping.get(status, PurchaseRequest.status_label(status))


def _status_sort_key(label: str) -> tuple[int, str]:
    if label in STATUS_PRIORITY:
        return (0, str(STATUS_PRIORITY.index(label)))
    if label == "Other":
        return (2, label)
    return (1, label)


def build_materials_summary() -> dict[str, object]:
    raw_rows = (
        db.session.query(
            PurchaseRequest.status,
            func.count(PurchaseRequest.id),
            func.coalesce(func.sum(PurchaseRequest.quantity), 0),
        )
        .group_by(PurchaseRequest.status)
        .all()
    )

    grouped: dict[str, dict[str, object]] = defaultdict(
        lambda: {"status": "", "count": 0, "qty_total": 0.0, "status_values": set()}
    )
    for status, count, qty_total in raw_rows:
        label = status_display_label(status)
        entry = grouped[label]
        entry["status"] = label
        entry["count"] = int(entry["count"]) + int(count or 0)
        entry["qty_total"] = float(entry["qty_total"]) + float(qty_total or 0)
        if status:
            entry["status_values"].add(status)

    by_status: list[dict[str, object]] = []
    for entry in grouped.values():
        status_values = sorted(entry["status_values"])
        status_filter = None
        if status_values and all(value in PurchaseRequest.status_values() for value in status_values):
            status_filter = ",".join(status_values)
        by_status.append(
            {
                "status": entry["status"],
                "count": int(entry["count"]),
                "qty_total": round(float(entry["qty_total"]), 2),
                "status_values": status_values,
                "status_filter": status_filter,
            }
        )

    by_status.sort(key=lambda item: _status_sort_key(item["status"]))

    total_count = sum(item["count"] for item in by_status)
    total_qty = round(sum(float(item["qty_total"]) for item in by_status), 2)

    return {
        "by_status": by_status,
        "total_count": total_count,
        "total_qty": total_qty,
        "last_updated": datetime.utcnow().isoformat(),
    }


def build_open_shortage_counts(date_range: Iterable[date]) -> List[int]:
    date_list = list(date_range)
    if not date_list:
        return []

    start_date = date_list[0]
    end_date = date_list[-1]
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date, time.max)

    open_statuses = set(PurchaseRequest.status_values()) - {
        PurchaseRequest.STATUS_RECEIVED,
        PurchaseRequest.STATUS_CANCELLED,
    }

    counts = {day: 0 for day in date_list}
    requests = (
        PurchaseRequest.query.filter(PurchaseRequest.created_at >= start_dt)
        .filter(PurchaseRequest.created_at <= end_dt)
        .all()
    )
    for request in requests:
        if request.status not in open_statuses:
            continue
        created_date = request.created_at.date() if request.created_at else None
        if created_date in counts:
            counts[created_date] += 1

    return [counts[day] for day in date_list]
