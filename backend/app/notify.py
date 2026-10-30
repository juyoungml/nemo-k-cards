"""Emails to people (#22 first; #28 adds post alerts). One outgoing path, so every alert reads the same way and tests
can capture it.

Delivery is SMTP (`SMTP_URL`, e.g. `smtps://user:password@smtp.example.com:465` or `smtp://…:587` with STARTTLS;
`MAIL_FROM` is the sender). On a server without `SMTP_URL` an alert is logged at ERROR level and reported as not
emailed, never silently dropped. Tests set `transport` and read what would have been sent.

Sending happens after the database transaction that decided it has committed, so a mail is never sent for a change
that rolled back. A failed send is logged and doesn't undo the change."""

import logging
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import unquote, urlparse
from uuid import UUID

from app import store
from app.config import settings

log = logging.getLogger("notify")


@dataclass(frozen=True)
class Email:
    to: tuple[str, ...]
    subject: str
    text: str


# Tests replace this with a function that records the Email; nothing in the test suite reaches a mail server.
transport: Callable[[Email], None] | None = None


def admin_emails(org_id: UUID) -> list[str]:
    """The Organization's Admins who have an email address on file. Privileged: alerts are sent by background work
    that has no signed-in person."""
    with store.system() as c:
        rows = c.execute("select email from members where org_id = %s and role = 'admin' and email is not null "
                         "order by created_at", (org_id,)).fetchall()
    return [r[0] for r in rows]


def _smtp(email: Email) -> None:
    u = urlparse(settings.smtp_url)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.mail_from, ", ".join(email.to), email.subject
    msg.set_content(email.text)
    secure = u.scheme == "smtps"
    port = u.port or (465 if secure else 587)
    ctx = ssl.create_default_context()
    with (smtplib.SMTP_SSL(u.hostname, port, context=ctx, timeout=20) if secure
          else smtplib.SMTP(u.hostname, port, timeout=20)) as s:
        if not secure:
            s.starttls(context=ctx)
        if u.username:
            s.login(unquote(u.username), unquote(u.password or ""))
        s.send_message(msg)


def send(email: Email) -> bool:
    """True once the mail is handed to the mail server (or the test transport)."""
    if not email.to:
        log.error("ALERT with no recipient (no Admin email on file): %s", email.subject)
        return False
    if transport is not None:
        transport(email)
        return True
    if not settings.smtp_url:
        log.error("ALERT not emailed (SMTP_URL is not set) to %s: %s", ", ".join(email.to), email.subject)
        return False
    try:
        _smtp(email)
        return True
    except (OSError, smtplib.SMTPException) as e:
        log.error("ALERT email failed (%s) to %s: %s", type(e).__name__, ", ".join(email.to), email.subject)
        return False


def alert_admins(org_id: UUID, subject: str, text: str) -> bool:
    footer = f"\n\nOpen the Instagram page: {settings.admin_url.rstrip('/')}/instagram\n"
    return send(Email(tuple(admin_emails(org_id)), subject, text + footer))
