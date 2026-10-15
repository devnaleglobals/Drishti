# """Email delivery for alerts and reports.

# Configure ONE of:
#   SMTP:   SMTP_HOST, SMTP_PORT (587 or 465), SMTP_USER, SMTP_PASSWORD, SMTP_FROM
#   Resend: RESEND_API_KEY, MAIL_FROM          (HTTPS API, works where SMTP ports are blocked)
# """
# from __future__ import annotations

# import base64
# import os
# import smtplib
# import ssl
# from email.message import EmailMessage

# import requests


# def method() -> str:
#     if os.getenv("RESEND_API_KEY"):
#         return "resend"
#     if os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"):
#         return "smtp"
#     return ""


# def send(to: list[str], subject: str, html: str, text: str = "", attachment: tuple | None = None) -> tuple[bool, str]:
#     """attachment: (filename, bytes, mime_subtype e.g. 'html'). Returns (ok, message)."""
#     to = [t for t in to if t and "@" in t]
#     if not to:
#         return False, "No recipients configured"
#     how = method()
#     if not how:
#         return False, "Email is not configured on the server"
#     try:
#         if how == "resend":
#             payload = {"from": os.getenv("MAIL_FROM") or os.getenv("SMTP_FROM") or "Drishti <alerts@resend.dev>",
#                        "to": to, "subject": subject, "html": html}
#             if text:
#                 payload["text"] = text
#             if attachment:
#                 payload["attachments"] = [{"filename": attachment[0],
#                                            "content": base64.b64encode(attachment[1]).decode()}]
#             r = requests.post("https://api.resend.com/emails", json=payload, timeout=20,
#                               headers={"Authorization": f"Bearer {os.getenv('RESEND_API_KEY')}"})
#             if r.status_code >= 300:
#                 return False, f"Email service error {r.status_code}: {r.text[:160]}"
#             return True, f"Sent to {len(to)} recipient(s)"

#         msg = EmailMessage()
#         msg["Subject"] = subject
#         msg["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER")
#         msg["To"] = ", ".join(to)
#         msg.set_content(text or "This message is best viewed in an email client that shows HTML.")
#         msg.add_alternative(html, subtype="html")
#         if attachment:
#             msg.add_attachment(attachment[1], maintype="text", subtype=attachment[2], filename=attachment[0])
#         host, port = os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587"))
#         ctx = ssl.create_default_context()
#         if port == 465:
#             with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as s:
#                 s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
#                 s.send_message(msg)
#         else:
#             with smtplib.SMTP(host, port, timeout=20) as s:
#                 s.starttls(context=ctx)
#                 s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
#                 s.send_message(msg)
#         return True, f"Sent to {len(to)} recipient(s)"
#     except Exception as e:
#         return False, f"Email failed: {str(e)[:180]}"




"""Email delivery for alerts and reports.

Configure ONE of:

SMTP:
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=465 or 587
    SMTP_USER=your_email@gmail.com
    SMTP_PASSWORD=your_google_app_password
    SMTP_FROM=your_email@gmail.com

Resend:
    RESEND_API_KEY=re_xxxxxxxxx
    MAIL_FROM=Drishti <alerts@your-verified-domain.com>

Gmail App Passwords can be entered with or without spaces.
Resend uses HTTPS and is useful when hosting blocks outbound SMTP.
"""

from __future__ import annotations

import base64
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr

import requests


def _env(name: str, default: str = "") -> str:
    """Read an environment variable and trim surrounding whitespace."""
    return os.getenv(name, default).strip()


def _smtp_password() -> str:
    """Normalize an App Password, accepting spaces between characters."""
    raw = os.getenv("SMTP_PASSWORD", "")
    return re.sub(r"\s+", "", raw)


def method() -> str:
    """Prefer Resend when configured; otherwise use SMTP."""
    if _env("RESEND_API_KEY"):
        return "resend"

    if (
        _env("SMTP_HOST")
        and _env("SMTP_USER")
        and _smtp_password()
    ):
        return "smtp"

    return ""


def _validate_recipients(to: list[str]) -> list[str]:
    """Remove empty recipient entries."""
    return [
        address.strip()
        for address in to
        if address and "@" in address.strip()
    ]


def _send_resend(
    to: list[str],
    subject: str,
    html: str,
    text: str,
    attachment: tuple | None,
) -> tuple[bool, str]:
    """Send email through Resend's HTTPS API."""
    api_key = _env("RESEND_API_KEY")
    sender = _env("MAIL_FROM") or _env("SMTP_FROM")

    if not sender:
        return False, "Configure MAIL_FROM with a verified sender address"

    payload = {
        "from": sender,
        "to": to,
        "subject": subject,
        "html": html,
    }

    if text:
        payload["text"] = text

    if attachment:
        filename, content, mime_subtype = attachment
        payload["attachments"] = [
            {
                "filename": filename,
                "content": base64.b64encode(content).decode("ascii"),
            }
        ]

    response = requests.post(
        "https://api.resend.com/emails",
        json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=20,
    )

    if response.status_code >= 300:
        # Avoid returning sensitive request headers or API keys.
        return (
            False,
            f"Resend error {response.status_code}: "
            f"{response.text[:250]}",
        )

    return True, f"Email accepted by Resend for {len(to)} recipient(s)"


def _send_smtp(
    to: list[str],
    subject: str,
    html: str,
    text: str,
    attachment: tuple | None,
) -> tuple[bool, str]:
    """Send email through SMTP with SSL or STARTTLS."""
    host = _env("SMTP_HOST")
    username = _env("SMTP_USER")
    password = _smtp_password()
    sender = _env("SMTP_FROM") or username

    try:
        port = int(_env("SMTP_PORT", "465"))
    except ValueError:
        return False, "SMTP_PORT must be 465 or 587"

    if port not in (465, 587):
        return False, "Use SMTP port 465 (SSL) or 587 (STARTTLS)"

    if not host or not username or not password:
        return False, "SMTP host, username, and password are required"

    if not parseaddr(sender)[1]:
        return False, "SMTP_FROM must contain a valid sender email address"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(to)

    message.set_content(
        text
        or "This message is best viewed in an email client that supports HTML."
    )
    message.add_alternative(html, subtype="html")

    if attachment:
        filename, content, mime_subtype = attachment

        # Preserve the original interface: attachment is
        # (filename, bytes, MIME subtype).
        message.add_attachment(
            content,
            maintype="text",
            subtype=mime_subtype,
            filename=filename,
        )

    context = ssl.create_default_context()

    if port == 465:
        with smtplib.SMTP_SSL(
            host,
            port,
            context=context,
            timeout=20,
        ) as server:
            server.ehlo()
            server.login(username, password)
            server.send_message(message)

    else:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(username, password)
            server.send_message(message)

    return True, f"Email sent to {len(to)} recipient(s)"


def send(
    to: list[str],
    subject: str,
    html: str,
    text: str = "",
    attachment: tuple | None = None,
) -> tuple[bool, str]:
    """Send an email and return (success, status_message).

    attachment:
        (filename, bytes, MIME subtype, e.g. 'html' or 'csv')
    """
    recipients = _validate_recipients(to)

    if not recipients:
        return False, "No valid recipients configured"

    selected_method = method()

    if not selected_method:
        return (
            False,
            "Email is not configured. Set Resend API variables "
            "or SMTP_HOST, SMTP_USER, and SMTP_PASSWORD.",
        )

    try:
        if selected_method == "resend":
            return _send_resend(
                recipients,
                subject,
                html,
                text,
                attachment,
            )

        return _send_smtp(
            recipients,
            subject,
            html,
            text,
            attachment,
        )

    except smtplib.SMTPAuthenticationError as exc:
        return (
            False,
            f"Gmail SMTP authentication failed "
            f"(code {exc.smtp_code}). Verify the Gmail address, "
            "Google App Password, and account settings.",
        )

    except (smtplib.SMTPConnectError, TimeoutError, ConnectionError) as exc:
        return (
            False,
            f"SMTP connection failed ({type(exc).__name__}). "
            "Check the hosting provider's outbound SMTP restrictions "
            "and network connectivity.",
        )

    except smtplib.SMTPServerDisconnected:
        return (
            False,
            "SMTP server disconnected unexpectedly. Check hosting "
            "network restrictions and SMTP/TLS configuration.",
        )

    except requests.RequestException as exc:
        return (
            False,
            f"Resend API request failed ({type(exc).__name__}). "
            "Check outbound HTTPS connectivity.",
        )

    except Exception as exc:
        # Return a concise diagnostic without exposing credentials.
        return False, f"Email failed: {type(exc).__name__}: {str(exc)[:150]}"
