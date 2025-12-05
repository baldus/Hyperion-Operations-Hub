"""Routes for generating MDI email content."""
from __future__ import annotations

import base64
from datetime import datetime
from typing import List

from flask import jsonify, url_for
from sqlalchemy import case

from invapp.mdi.email_utils import build_eml_message, render_mdi_email_html, suggested_recipients
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
    entries = _active_entries()
    subject = f"Daily MDI Active Item Summary – {datetime.utcnow().date().isoformat()}"

    dashboard_url = url_for("mdi.meeting_view", _external=True)
    html_body = render_mdi_email_html(
        entries,
        dashboard_url=dashboard_url,
        item_link_factory=lambda entry: url_for("mdi.mdi_item_redirect", entry_id=entry.id, _external=True),
    )

    recipients = suggested_recipients()
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


def register(bp):
    bp.add_url_rule("/generate_mdi_email", view_func=generate_mdi_email, methods=["POST"])
