"""Outbound email, over SMTP, from the standard library.

The product had no way to reach a *person* — Slack posts to a channel — so an
invitation could only be delivered by an administrator copying a link out of the
dashboard and pasting it somewhere. This is the transport that closes that.

``smtplib`` rather than a provider SDK, for the same reason ``hashlib.scrypt``
beat argon2 here: no new runtime dependency, and SMTP is the one interface every
provider offers. Resend, Google Workspace, Postmark and a company relay are all
just host/port/credentials.

Three rules, and the middle one is the reason this module exists at all rather
than three lines inside the route:

**Never fatal.** A mail failure must not cost the caller their invitation. The
invite row is written and committed *before* anything is sent, and every failure
here comes back as a value rather than an exception. The worst outcome is the
behaviour the product had yesterday: an administrator with a link to paste.

**Never claim more than happened.** ``send`` reports ``sent``, ``skipped`` or
``failed``, and the API passes that to the dashboard verbatim. A UI that says
"invitation emailed" while SMTP was refusing connections is worse than one that
says nothing, because the administrator stops watching. This is not hypothetical
— the same mistake shipped in another product here, where a handoff promised an
email before sending it and logged the failure at INFO where nobody looked.

**Never log the message.** An invitation body contains a live single-use
credential. The URL, the token and the rendered body are never passed to the
logger; only the recipient, the outcome and the error class are.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from app.logging_config import log_ctx
from app.settings import Settings

logger = logging.getLogger(__name__)

#: Nothing was attempted, because no transport is configured or there was
#: nobody to send to. Not an error: it is the product's default state.
SKIPPED = "skipped"
#: The SMTP server accepted the message. Not a promise that it was delivered —
#: nothing short of a bounce or a read receipt is, and this product has neither.
SENT = "sent"
#: A transport was configured and did not accept the message.
FAILED = "failed"


@dataclass(frozen=True)
class Delivery:
    """What happened, in a form the dashboard can render without interpreting."""

    status: str
    #: One sentence, written for the administrator reading it. Safe to display:
    #: it never contains the message body, the token or the credentials.
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == SENT


def is_configured(settings: Settings) -> bool:
    """A host and a From address are the minimum. Auth is optional.

    An unauthenticated relay on a private network is a perfectly ordinary
    setup, so a missing username is not a missing transport.
    """
    return bool(settings.smtp_host and settings.smtp_from)


def _connect(settings: Settings) -> smtplib.SMTP | smtplib.SMTP_SSL:
    """Open a connection, bounded by a timeout.

    The timeout is the point. A silently dropped SMTP connection otherwise hangs
    until the OS gives up, and because this is called from inside a request the
    administrator watches a spinner for minutes with no way to tell whether the
    invitation exists.
    """
    timeout = settings.smtp_timeout_seconds
    if settings.smtp_use_ssl:
        # Implicit TLS, usually 465. The context verifies certificates by default.
        return smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=timeout, context=ssl.create_default_context()
        )
    client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout)
    if settings.smtp_use_starttls:
        client.starttls(context=ssl.create_default_context())
    return client


def send(
    settings: Settings,
    *,
    to: str,
    subject: str,
    body: str,
) -> Delivery:
    """Deliver one plain-text message. Returns what happened; never raises.

    Plain text only, deliberately. An HTML invitation buys nothing here and
    costs a second body to keep in step, more ways to render badly, and a
    stronger spam signal from a domain that sends almost nothing.
    """
    if not is_configured(settings):
        return Delivery(SKIPPED, "No mail server is configured, so nothing was emailed.")
    if not to.strip():
        return Delivery(SKIPPED, "This invitation has no address, so there was nobody to email.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = (
        formataddr((settings.smtp_from_name, settings.smtp_from))
        if settings.smtp_from_name
        else settings.smtp_from
    )
    message["To"] = to
    # Without an explicit id smtplib generates one from the local hostname,
    # which inside a container is a random hex string and reads as spam.
    message["Message-ID"] = make_msgid(domain=settings.smtp_from.partition("@")[2] or None)
    message.set_content(body)

    try:
        with _connect(settings) as client:
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        # The recipient and the failure class only. The body holds a live
        # single-use token and must not reach the log.
        log_ctx(
            logger,
            logging.WARNING,
            "invitation email failed",
            to=to,
            error=type(exc).__name__,
        )
        return Delivery(FAILED, f"The mail server refused the message ({type(exc).__name__}).")

    log_ctx(logger, logging.INFO, "invitation email sent", to=to)
    return Delivery(SENT, f"Emailed to {to}.")


def invitation_body(*, url: str, inviter: str, role: str, expires: str, app_url: str) -> str:
    """The message an invitee receives.

    Says who invited them and what it is, because a bare link from an unfamiliar
    domain is indistinguishable from phishing — which is exactly the reflex you
    want people to have about bare links, so the mail has to earn the click.
    """
    role_line = (
        "You will be an administrator, so you can invite other people and change roles."
        if role == "admin"
        else "You will be a member."
    )
    return (
        f"{inviter} has invited you to Tender Monitor, the dashboard that watches public\n"
        f"procurement notices for SDS and EHS software work.\n"
        f"\n"
        f"Set up your account here:\n"
        f"\n"
        f"    {url}\n"
        f"\n"
        f"{role_line}\n"
        f"\n"
        f"The link works once and expires on {expires}. It is tied to this address, so\n"
        f"forwarding it will not work.\n"
        f"\n"
        f"If you were not expecting this, ignore it — nothing happens until you use the\n"
        f"link, and it stops working on its own.\n"
        f"\n"
        f"{app_url}\n"
    )
