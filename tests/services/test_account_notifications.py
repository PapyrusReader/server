"""Account mail is safe to render, transactional, retryable and never restores access by link."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.config import get_settings
from papyrus.core.exceptions import ServiceUnavailableError
from papyrus.models import AccountNotification, User
from papyrus.services import account_notifications, email


@pytest.fixture
def smtp_messages(monkeypatch: pytest.MonkeyPatch) -> list[EmailMessage]:
    settings = get_settings()
    monkeypatch.setattr(settings, "email_delivery_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_from_email", "noreply@example.test")
    monkeypatch.setattr(settings, "smtp_username", None)
    monkeypatch.setattr(settings, "smtp_password", None)
    monkeypatch.setattr(settings, "smtp_use_ssl", False)
    monkeypatch.setattr(settings, "smtp_use_tls", False)
    monkeypatch.setattr(settings, "app_public_base_url", "https://reader.example.test")
    messages: list[EmailMessage] = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def send_message(self, message):
            messages.append(message)

    monkeypatch.setattr(email.smtplib, "SMTP", SMTP)
    return messages


def test_notifications_have_html_and_plain_text_with_escaped_content(smtp_messages):
    deadline = datetime(2026, 11, 22, 14, 4, tzinfo=UTC)
    content = account_notifications.render_account_email('<script>alert("name")</script>', deadline)
    email.send_email("reader@example.test", content.subject, content.text, html_body=content.html)
    message = smtp_messages[0]
    assert message.get_content_type() == "multipart/alternative"
    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "November 22, 2026 at 14:04 UTC" in plain
    assert "November 22, 2026 at 14:04 UTC" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'href="https://reader.example.test/login"' in html
    assert "authorization_token" not in html
    assert "token=" not in html
    email.send_email("reader@example.test", "Existing plain text", "Unchanged plain email")
    assert smtp_messages[1].get_content_type() == "text/plain"


def test_recovery_email_has_no_deadline_and_handles_missing_or_unsafe_urls(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_public_base_url", None)
    monkeypatch.setattr(settings, "public_base_url", None)
    content = account_notifications.render_account_email("Reader", None)
    assert "cancelled" in content.subject
    assert "No further action is needed" in content.text
    assert "RECOVERY DEADLINE" not in content.html
    assert "href=" not in content.html
    monkeypatch.setattr(settings, "app_public_base_url", "javascript:alert(1)")
    assert "href=" not in account_notifications.render_account_email("Reader", None).html
    monkeypatch.setattr(settings, "app_public_base_url", None)
    monkeypatch.setattr(settings, "public_base_url", "https://server.example.test")
    content = account_notifications.render_account_email("Reader", None)
    assert f"https://server.example.test{settings.api_prefix}/account-deletion" in content.html


async def test_scheduling_and_recovery_queue_and_deliver_matching_notices(
    client: AsyncClient, auth_user, auth_headers, db_session: AsyncSession, smtp_messages
):
    response = await client.post("/v1/account-deletion/current", headers=auth_headers)
    assert response.status_code == 200
    assert smtp_messages == []
    notice = await db_session.scalar(select(AccountNotification))
    assert notice.recipient == "user@example.com"
    assert "scheduled for deletion" in notice.subject
    assert await account_notifications.deliver_pending_notifications(db_session) == 1
    assert len(smtp_messages) == 1
    assert await db_session.scalar(select(func.count()).select_from(AccountNotification)) == 0
    login = await client.post("/v1/auth/login", json={"email": "user@example.com", "password": auth_user["password"]})
    proof = login.json()["error"]["details"]["authorization"]["authorization_token"]
    response = await client.post("/v1/account-deletion/cancel", json={"authorization_token": proof})
    assert response.status_code == 200
    restored_headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    assert (await client.get("/v1/users/me", headers=restored_headers)).status_code == 200
    assert await account_notifications.deliver_pending_notifications(db_session) == 1
    assert len(smtp_messages) == 2
    assert "cancelled" in smtp_messages[1]["Subject"]
    assert (await client.post("/v1/account-deletion/cancel", json={"authorization_token": proof})).status_code == 401
    assert await account_notifications.deliver_pending_notifications(db_session) == 0


async def test_delivery_failure_retries_before_sending_later_recovery_notice(
    client: AsyncClient, auth_user, auth_headers, db_session: AsyncSession, smtp_messages, monkeypatch
):
    original_send = email.send_email

    def fail(*args, **kwargs):
        raise ServiceUnavailableError("SMTP unavailable")

    monkeypatch.setattr(email, "send_email", fail)
    assert (await client.post("/v1/account-deletion/current", headers=auth_headers)).status_code == 200
    assert await account_notifications.deliver_pending_notifications(db_session) == 0
    notice = await db_session.scalar(select(AccountNotification))
    old_id = notice.notification_id
    assert notice.attempts == 1
    assert notice.next_attempt_at > datetime.now(UTC)
    assert await account_notifications.deliver_pending_notifications(db_session) == 0
    login = await client.post("/v1/auth/login", json={"email": "user@example.com", "password": auth_user["password"]})
    proof = login.json()["error"]["details"]["authorization"]["authorization_token"]
    assert (await client.post("/v1/account-deletion/cancel", json={"authorization_token": proof})).status_code == 200
    db_session.expire_all()
    notices = (await db_session.scalars(select(AccountNotification).order_by(AccountNotification.created_at))).all()
    assert len(notices) == 2
    assert notices[0].notification_id == old_id
    assert "cancelled" in notices[1].subject
    assert await account_notifications.deliver_pending_notifications(db_session) == 0
    notice = await db_session.get(AccountNotification, old_id)
    notice.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    monkeypatch.setattr(email, "send_email", original_send)
    assert await account_notifications.deliver_pending_notifications(db_session) == 2
    assert len(smtp_messages) == 2
    assert "scheduled for deletion" in smtp_messages[0]["Subject"]
    assert "cancelled" in smtp_messages[1]["Subject"]


async def test_notices_roll_back_expire_and_cascade_with_account(db_session, auth_user, smtp_messages):
    user = await db_session.get(User, UUID(auth_user["user_id"]))
    await account_notifications.queue_account_notification(db_session, user)
    await db_session.rollback()
    assert await db_session.scalar(select(func.count()).select_from(AccountNotification)) == 0
    await db_session.refresh(user)
    await account_notifications.queue_account_notification(db_session, user)
    await db_session.commit()
    notice = await db_session.scalar(select(AccountNotification))
    notice.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    assert await account_notifications.deliver_pending_notifications(db_session) == 0
    assert smtp_messages == []
    await db_session.refresh(user)
    await account_notifications.queue_account_notification(db_session, user)
    await db_session.commit()
    await db_session.delete(user)
    await db_session.commit()
    assert await db_session.scalar(select(func.count()).select_from(AccountNotification)) == 0


@pytest.mark.parametrize("verified,email_address", [(False, "reader@example.test"), (True, None)])
async def test_only_verified_contact_addresses_receive_notices(db_session, auth_user, verified, email_address):
    user = await db_session.get(User, UUID(auth_user["user_id"]))
    user.primary_email_verified = verified
    user.primary_email = email_address
    await account_notifications.queue_account_notification(db_session, user)
    await db_session.commit()
    assert await db_session.scalar(select(func.count()).select_from(AccountNotification)) == 0


async def test_delivery_lease_prevents_duplicate_workers_without_blocking_recovery(
    client, auth_user, auth_headers, test_session_maker, smtp_messages, monkeypatch
):
    started = threading.Event()
    release = threading.Event()
    original_send = email.send_email

    def slow_send(*args, **kwargs):
        started.set()
        assert release.wait(timeout=10)
        original_send(*args, **kwargs)

    monkeypatch.setattr(email, "send_email", slow_send)
    assert (await client.post("/v1/account-deletion/current", headers=auth_headers)).status_code == 200

    async with test_session_maker() as first, test_session_maker() as second:
        delivery = asyncio.create_task(account_notifications.deliver_pending_notifications(first))

        try:
            assert await asyncio.to_thread(started.wait, 5)
            assert await account_notifications.deliver_pending_notifications(second) == 0

            login = await client.post(
                "/v1/auth/login", json={"email": "user@example.com", "password": auth_user["password"]}
            )

            proof = login.json()["error"]["details"]["authorization"]["authorization_token"]

            response = await asyncio.wait_for(
                client.post("/v1/account-deletion/cancel", json={"authorization_token": proof}), timeout=3
            )

            assert response.status_code == 200
            assert await account_notifications.deliver_pending_notifications(second) == 0
        finally:
            release.set()
            await delivery

    assert len(smtp_messages) == 2
    assert "scheduled for deletion" in smtp_messages[0]["Subject"]
    assert "cancelled" in smtp_messages[1]["Subject"]


async def test_rapid_delete_then_keep_sends_both_notices_in_order(
    client, auth_user, auth_headers, db_session, smtp_messages
):
    assert (await client.post("/v1/account-deletion/current", headers=auth_headers)).status_code == 200
    login = await client.post("/v1/auth/login", json={"email": "user@example.com", "password": auth_user["password"]})
    proof = login.json()["error"]["details"]["authorization"]["authorization_token"]
    assert (await client.post("/v1/account-deletion/cancel", json={"authorization_token": proof})).status_code == 200
    assert smtp_messages == []
    assert await db_session.scalar(select(func.count()).select_from(AccountNotification)) == 2
    assert await account_notifications.deliver_pending_notifications(db_session) == 2
    assert "scheduled for deletion" in smtp_messages[0]["Subject"]
    assert "cancelled" in smtp_messages[1]["Subject"]
