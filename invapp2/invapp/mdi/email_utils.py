"""Utilities for generating MDI email content without sending mail."""
from __future__ import annotations

from datetime import datetime
from email import policy
from email.message import EmailMessage
from typing import Callable, Iterable, List, Sequence

from flask import current_app, render_template

from invapp.mdi.models import MDIEntry


def suggested_recipients() -> List[str]:
    """Return a cleaned list of default recipients from configuration."""

    raw_value = current_app.config.get("MDI_DEFAULT_RECIPIENTS", "")

    if isinstance(raw_value, str):
        recipients: Sequence[str] = [part.strip() for part in raw_value.split(",") if part.strip()]
    elif isinstance(raw_value, (list, tuple, set)):
        recipients = [str(part).strip() for part in raw_value if str(part).strip()]
    else:
        recipients = []

    return list(recipients)


def render_mdi_email_html(
    entries: Iterable[MDIEntry],
    dashboard_url: str,
    item_link_factory: Callable[[MDIEntry], str],
) -> str:
    """Render the HTML body for the active-item summary email."""

    prepared_entries = [
        {
            "id": entry.id,
            "title": entry.item_description or entry.description or "MDI Item",
            "owner": entry.owner or "Unassigned",
            "due_date": entry.due_date,
            "status": entry.status or "Unknown",
            "description": entry.notes or entry.description or "No description provided.",
            "category": entry.category,
            "url": item_link_factory(entry),
        }
        for entry in entries
    ]

    return render_template(
        "email_mdi.html",
        entries=prepared_entries,
        dashboard_url=dashboard_url,
        generated_at=datetime.utcnow(),
    )


def build_eml_message(subject: str, recipients: Sequence[str], html_body: str) -> EmailMessage:
    """Create a standards-compliant email message containing the HTML body."""

    sender = current_app.config.get("MDI_DEFAULT_SENDER", "mdi-console@example.com")
    message = EmailMessage(policy=policy.default)
    message["Subject"] = subject
    message["From"] = sender
    if recipients:
        message["To"] = ", ".join(recipients)

    message.set_content(
        "Open this message in an HTML-capable mail client to view the MDI summary.",
    )
    message.add_alternative(html_body, subtype="html")
    return message
