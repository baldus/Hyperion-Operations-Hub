"""Routes for emailing MDI active item summaries."""
from __future__ import annotations

from typing import Iterable
from urllib.parse import urljoin

from flask import current_app, jsonify, redirect, request, url_for

from invapp.mdi.email_utils import EmailConfigurationError, render_and_send_active_items_email
from invapp.mdi.models import MDIEntry

from .constants import COMPLETED_STATUSES


def _build_absolute_url(path: str) -> str:
    """Return an absolute URL using the current request's host URL."""

    return urljoin(request.host_url, path.lstrip("/"))


def _serialize_entry(entry: MDIEntry) -> dict:
    """Convert an MDIEntry into a simplified dictionary for email rendering."""

    if entry.category == "Delivery":
        title = entry.item_description or entry.description or "Delivery Item"
    elif entry.category == "People":
        title = "People Update"
    else:
        title = entry.description or "No description provided"

    if entry.due_date:
        due_date = entry.due_date.strftime("%b %d, %Y")
    elif entry.date_logged:
        due_date = f"Logged {entry.date_logged.strftime('%b %d, %Y')}"
    else:
        due_date = "No due date"

    description = entry.description or entry.notes or ""

    return {
        "id": entry.id,
        "title": title,
        "owner": entry.owner or "Unassigned",
        "due_date": due_date,
        "status": entry.status or "N/A",
        "description": description,
    }


def send_mdi_email():
    """Compile active MDI items and send them via email."""

    active_entries: Iterable[MDIEntry] = MDIEntry.query.filter(MDIEntry.status.notin_(COMPLETED_STATUSES)).order_by(MDIEntry.date_logged.desc()).all()
    dashboard_url = _build_absolute_url(url_for("mdi.meeting_view"))

    try:
        active_items = [
            {
                **_serialize_entry(entry),
                "link": _build_absolute_url(url_for("mdi.view_item", entry_id=entry.id)),
            }
            for entry in active_entries
        ]
        render_and_send_active_items_email(active_items, dashboard_url)
    except EmailConfigurationError as exc:
        current_app.logger.warning("MDI email configuration issue: %s", exc)
        return jsonify({"message": str(exc)}), 400
    except Exception as exc:  # pragma: no cover - defensive logging
        current_app.logger.exception("Failed to send MDI active items email")
        return jsonify({"message": "Unable to send MDI email at this time."}), 500

    return jsonify({"message": "MDI active items email sent."})


def view_item(entry_id: int):
    """Provide a stable link for MDI items that redirects to the edit/view page."""

    return redirect(url_for("mdi.report_entry", id=entry_id))


def register(bp):
    bp.add_url_rule("/send_mdi_email", view_func=send_mdi_email, methods=["POST"], endpoint="send_mdi_email")
    bp.add_url_rule("/mdi/item/<int:entry_id>", view_func=view_item, methods=["GET"], endpoint="view_item")
