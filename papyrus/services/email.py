"""SMTP email delivery service."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from papyrus.config import get_settings
from papyrus.core.exceptions import ValidationError


def is_email_delivery_configured() -> bool:
    """Return whether SMTP-backed email delivery is configured."""
    settings = get_settings()
    return bool(settings.email_delivery_enabled and settings.smtp_host and settings.smtp_from_email)


def send_email(recipient: str, subject: str, body: str) -> None:
    """Send a plain-text email through the configured SMTP server."""
    settings = get_settings()

    if not is_email_delivery_configured():
        raise ValidationError("Email delivery is not configured on this server")

    message = EmailMessage()
    sender = settings.smtp_from_email

    if sender is None:
        raise ValidationError("Email delivery is not configured on this server")

    if settings.smtp_from_name:
        message["From"] = f"{settings.smtp_from_name} <{sender}>"
    else:
        message["From"] = sender

    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    smtp_host = settings.smtp_host

    if smtp_host is None:
        raise ValidationError("Email delivery is not configured on this server")

    if settings.smtp_use_ssl:
        context = ssl.create_default_context()

        with smtplib.SMTP_SSL(
            smtp_host,
            settings.smtp_port,
            context=context,
            timeout=10,
        ) as smtp:
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)

            smtp.send_message(message)

        return

    with smtplib.SMTP(smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_use_tls:
            context = ssl.create_default_context()
            smtp.starttls(context=context)

        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password)

        smtp.send_message(message)
