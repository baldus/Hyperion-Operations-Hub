"""Routes for generating MDI email content."""
from __future__ import annotations

import base64
from datetime import datetime
from typing import List

from flask import current_app, jsonify, url_for
from sqlalchemy import case

from invapp.mdi.email_utils import (
    build_eml_message,
    render_mdi_email_html,
    send_email_via_smtp,
    suggested_recipients,
)
from invapp.mdi.models import MDIEntry
from .constants import COMPLETED_STATUSES


def _active_entries() -> List[MDIEntry]:
    priority_case = case(
        (MDIEntry.priority == "High", 3),
        (MDIEntry.priority == "Medium", 2),
        (MDIEntry.priority == "Low", 1),
        else_=0,
    )

    return (
        MDIEntry.query.filter(MDIEntry.status.notin_(COMPLETED_STATUSES))
        .order_by(priority_case.desc(), MDIEntry.date_logged.desc())
        .all()
    )


def generate_mdi_email():
    entries, html_body, recipients, subject, dashboard_url = _build_email_payload()
    message = build_eml_message(subject=subject, recipients=recipients, html_body=html_body)
    eml_base64 = base64.b64encode(message.as_bytes()).decode("ascii")

    return jsonify(
        {
            "subject": subject,
            "recipients": recipients,
            "html_body": html_body,
            "eml_base64": eml_base64,
            "download_name": "mdi_active_items.eml",
            "dashboard_url": dashboard_url,
            "item_count": len(entries),
        }
    )


def send_mdi_email():
    entries, html_body, recipients, subject, dashboard_url = _build_email_payload()
    if not recipients:
        return (
            jsonify({"error": "No recipients configured. Set MDI_DEFAULT_RECIPIENTS to send notifications."}),
            400,
        )

    try:
        message = build_eml_message(
            subject=subject,
            recipients=recipients,
            html_body=html_body,
            draft=False,
        )
        send_email_via_smtp(message)
    except Exception as exc:  # noqa: BLE001 - surfaced to client as structured JSON
        current_app.logger.exception("Failed to send MDI summary email")
        return (
            jsonify({"error": "Unable to send email", "details": str(exc)}),
            500,
        )

    return jsonify(
        {
            "sent": True,
            "recipient_count": len(recipients),
            "item_count": len(entries),
            "subject": subject,
            "dashboard_url": dashboard_url,
        }
    )


def _build_email_payload():
    entries = _active_entries()
    subject = f"Daily MDI Active Item Summary – {datetime.utcnow().date().isoformat()}"

    dashboard_url = url_for("mdi.meeting_view", _external=True)
    html_body = render_mdi_email_html(
        entries,
        dashboard_url=dashboard_url,
        item_link_factory=lambda entry: url_for("mdi.mdi_item_redirect", entry_id=entry.id, _external=True),
    )

    recipients = suggested_recipients()
    return entries, html_body, recipients, subject, dashboard_url


def register(bp):
    bp.add_url_rule("/generate_mdi_email", view_func=generate_mdi_email, methods=["POST"])
    bp.add_url_rule(
        "/mdi/send_active_email",
        view_func=send_mdi_email,
        methods=["POST"],
        endpoint="send_mdi_email",
    )
