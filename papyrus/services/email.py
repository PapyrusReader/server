"""Shared transactional email styling and SMTP delivery."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from html import escape
from pathlib import Path
from string import Template

from papyrus.config import get_settings
from papyrus.core.exceptions import ServiceUnavailableError, ValidationError


def render_email_html(
    *,
    subject: str,
    preheader: str,
    heading: str,
    greeting: str,
    summary: str,
    guidance: str,
    security_note: str,
    action_label: str,
    action_url: str | None,
    category: str = "ACCOUNT UPDATE",
    detail: tuple[str, str] | None = None,
) -> str:
    """Escape message content and links inside the shared email layout."""
    detail_html = ""

    if detail is not None:
        label, value = detail

        detail_html = (
            '<div style="margin:0 0 24px;padding:20px;background:#f3f1fb;border-radius:10px;">'
            f'<p style="margin:0 0 8px;font-size:12px;letter-spacing:1px;color:#645f73;">{escape(label)}</p>'
            f'<p style="margin:0;font-size:18px;line-height:28px;font-weight:700;">{escape(value)}</p></div>'
        )

    action_html = ""

    if action_url is not None:
        escaped_url = escape(action_url, quote=True)

        action_html = (
            '<table role="presentation" cellspacing="0" cellpadding="0"><tr>'
            '<td style="border-radius:8px;background:#5654a8;text-align:center;">'
            f'<a href="{escaped_url}" style="display:inline-block;padding:14px 24px;border:1px solid #5654a8;'
            'border-radius:8px;color:#ffffff;font-size:16px;line-height:24px;font-weight:700;text-decoration:none;">'
            f"{escape(action_label)}</a></td></tr></table>"
            '<p style="margin:16px 0 0;font-size:12px;line-height:20px;color:#726c86;overflow-wrap:anywhere;">'
            f'Or open <a href="{escaped_url}" style="color:#5654a8;word-break:break-all;">{escaped_url}</a></p>'
        )

    template = Template((Path(__file__).parents[1] / "templates" / "transactional_email.html").read_text())

    return template.substitute(
        subject=escape(subject),
        preheader=escape(preheader),
        category=escape(category),
        heading=escape(heading),
        greeting=escape(greeting),
        summary=escape(summary),
        detail=detail_html,
        guidance=escape(guidance),
        action=action_html,
        security_note=escape(security_note),
    )


def is_email_delivery_configured() -> bool:
    """Return whether SMTP-backed email delivery is configured."""
    settings = get_settings()
    return bool(settings.email_delivery_enabled and settings.smtp_host and settings.smtp_from_email)


def send_email(recipient: str, subject: str, body: str, *, html_body: str | None = None) -> None:
    """Send plain text with an optional HTML alternative through the configured SMTP server."""
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

    if html_body is not None:
        message.add_alternative(html_body, subtype="html")

    smtp_host = settings.smtp_host

    if smtp_host is None:
        raise ValidationError("Email delivery is not configured on this server")

    try:
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
    except (OSError, smtplib.SMTPException) as exc:
        raise ServiceUnavailableError("Email delivery failed") from exc
