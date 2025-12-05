"""Email utilities for sending MDI active item summaries."""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Iterable, Sequence

from flask import current_app, render_template


class EmailConfigurationError(RuntimeError):
    """Raised when the email configuration is incomplete."""


def _validate_recipients(recipients: Sequence[str]) -> list[str]:
    valid_recipients = [recipient.strip() for recipient in recipients if recipient and recipient.strip()]
    if not valid_recipients:
        raise EmailConfigurationError("No email recipients configured for MDI notifications.")
    return valid_recipients


def build_active_items_email(active_items: Iterable[dict], dashboard_url: str) -> str:
    """Render the HTML body for the MDI active items email.

    Args:
        active_items: Iterable of dictionaries describing active MDI items.
        dashboard_url: Absolute URL to the MDI dashboard.

    Returns:
        Rendered HTML content as a string.
    """

    return render_template(
        "mdi/email_mdi.html", active_items=list(active_items), dashboard_url=dashboard_url
    )


def send_email(subject: str, html_body: str, recipients: Sequence[str]) -> None:
    """Send an HTML email using SMTP settings from the Flask configuration.

    Args:
        subject: Subject line for the email.
        html_body: Pre-rendered HTML body.
        recipients: Iterable of recipient email addresses.

    Raises:
        EmailConfigurationError: If required configuration values are missing.
    """

    config = current_app.config
    smtp_server = config.get("MDI_SMTP_SERVER")
    smtp_port = int(config.get("MDI_SMTP_PORT", 587) or 587)
    smtp_username = config.get("MDI_SMTP_USERNAME")
    smtp_password = config.get("MDI_SMTP_PASSWORD")
    smtp_sender = config.get("MDI_EMAIL_SENDER") or smtp_username
    use_tls = str(config.get("MDI_SMTP_USE_TLS", True)).lower() != "false"

    if not smtp_server or not smtp_sender:
        raise EmailConfigurationError("SMTP server and sender must be configured to send email.")

    recipient_list = _validate_recipients(recipients)

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = smtp_sender
    message["To"] = ", ".join(recipient_list)
    message.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as smtp:
        if use_tls:
            smtp.starttls()
        if smtp_username and smtp_password:
            smtp.login(smtp_username, smtp_password)
        smtp.sendmail(smtp_sender, recipient_list, message.as_string())


def render_and_send_active_items_email(active_items: Iterable[dict], dashboard_url: str) -> None:
    """Render the active items email and send it to configured recipients."""

    recipients = current_app.config.get("MDI_EMAIL_RECIPIENTS", "")
    recipient_list = [email.strip() for email in recipients.split(",") if email.strip()]
    html_body = build_active_items_email(active_items, dashboard_url)
    send_email("Daily MDI Active Item Summary", html_body, recipient_list)
