"""Utilities for generating and sending MDI email content."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.message import EmailMessage
import smtplib
from ssl import create_default_context
from typing import Callable, Iterable, List, Sequence

from flask import current_app, render_template

from invapp.mdi.models import CATEGORY_DISPLAY, MDIEntry


@dataclass
class SMTPConfig:
    host: str
    port: int
    username: str | None
    password: str | None
    use_tls: bool
    use_ssl: bool
    sender: str | None


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_smtp_config() -> SMTPConfig:
    """Build an SMTP configuration object from Flask settings."""

    host = current_app.config.get("MDI_SMTP_HOST") or ""
    if not host:
        raise RuntimeError("MDI_SMTP_HOST must be configured to send email")

    sender = current_app.config.get("MDI_DEFAULT_SENDER") or None
    username = current_app.config.get("MDI_SMTP_USERNAME") or None

    return SMTPConfig(
        host=host,
        port=int(current_app.config.get("MDI_SMTP_PORT", 587)),
        username=username,
        password=current_app.config.get("MDI_SMTP_PASSWORD") or None,
        use_tls=_as_bool(current_app.config.get("MDI_SMTP_USE_TLS"), default=True),
        use_ssl=_as_bool(current_app.config.get("MDI_SMTP_USE_SSL"), default=False),
        sender=sender or username,
    )

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
    template_name: str = "email_mdi_summary.html",
) -> str:
    """Render the HTML body for the active-item summary email."""

    prepared_entries = [
        {
            "id": entry.id,
            "title": entry.item_description or entry.description or "MDI Item",
            "owner": entry.owner or "Unassigned",
            "due_date": entry.due_date,
            "date_logged": entry.date_logged,
            "status": entry.status or "Unknown",
            "priority": entry.priority,
            "area": entry.area,
            "related_reference": entry.related_reference,
            "description": entry.notes or entry.description or "No description provided.",
            "order_number": entry.order_number,
            "customer": entry.customer,
            "number_absentees": entry.number_absentees,
            "open_positions": entry.open_positions,
            "item_part_number": entry.item_part_number,
            "vendor": entry.vendor,
            "eta": entry.eta,
            "po_number": entry.po_number,
            "category": entry.category,
            "url": item_link_factory(entry),
        }
        for entry in entries
    ]

    ordered_categories = list(CATEGORY_DISPLAY.keys())
    category_sections = [
        {
            "name": category,
            "entries": [item for item in prepared_entries if item.get("category") == category],
            "meta": CATEGORY_DISPLAY.get(category, {}),
        }
        for category in ordered_categories
    ]

    return render_template(
        template_name,
        category_sections=category_sections,
        dashboard_url=dashboard_url,
        generated_at=datetime.utcnow(),
    )


def build_eml_message(
    subject: str,
    recipients: Sequence[str],
    html_body: str,
    *,
    draft: bool = True,
) -> EmailMessage:
    """Create a standards-compliant email message containing the HTML body."""

    sender = current_app.config.get("MDI_DEFAULT_SENDER") or None
    message = EmailMessage(policy=policy.default)
    if draft:
        # Mark as draft for Outlook/desktop clients so it opens ready to send instead of as a
        # received message. The X-Unsent header is understood by Outlook and other clients.
        message["X-Unsent"] = "1"
    message["Subject"] = subject
    if sender:
        message["From"] = sender
    if recipients:
        message["To"] = ", ".join(recipients)

    message.set_content(
        "Open this message in an HTML-capable mail client to view the MDI summary.",
    )
    message.add_alternative(html_body, subtype="html")
    return message


def send_email_via_smtp(message: EmailMessage, smtp_config: SMTPConfig | None = None) -> None:
    """Send the provided message using the configured SMTP server."""

    config = smtp_config or load_smtp_config()
    if not config.sender:
        raise RuntimeError("MDI_DEFAULT_SENDER or SMTP username must be configured for sending email")

    if "From" not in message:
        message["From"] = config.sender

    smtp_class = smtplib.SMTP_SSL if config.use_ssl else smtplib.SMTP
    with smtp_class(config.host, config.port, timeout=10) as client:
        client.ehlo()
        if config.use_tls and not config.use_ssl:
            client.starttls(context=create_default_context())
            client.ehlo()
        if config.username and config.password:
            client.login(config.username, config.password)
        client.send_message(message)
