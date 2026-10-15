"""Email delivery for alerts and reports.

Configure ONE of:
  SMTP:   SMTP_HOST, SMTP_PORT (587 or 465), SMTP_USER, SMTP_PASSWORD, SMTP_FROM
  Resend: RESEND_API_KEY, MAIL_FROM          (HTTPS API, works where SMTP ports are blocked)
"""
from __future__ import annotations

import base64
import os
import smtplib
import ssl
from email.message import EmailMessage

import requests


def method() -> str:
    if os.getenv("RESEND_API_KEY"):
        return "resend"
    if os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"):
        return "smtp"
    return ""


def send(to: list[str], subject: str, html: str, text: str = "", attachment: tuple | None = None) -> tuple[bool, str]:
    """attachment: (filename, bytes, mime_subtype e.g. 'html'). Returns (ok, message)."""
    to = [t for t in to if t and "@" in t]
    if not to:
        return False, "No recipients configured"
    how = method()
    if not how:
        return False, "Email is not configured on the server"
    try:
        if how == "resend":
            payload = {"from": os.getenv("MAIL_FROM") or os.getenv("SMTP_FROM") or "Drishti <alerts@resend.dev>",
                       "to": to, "subject": subject, "html": html}
            if text:
                payload["text"] = text
            if attachment:
                payload["attachments"] = [{"filename": attachment[0],
                                           "content": base64.b64encode(attachment[1]).decode()}]
            r = requests.post("https://api.resend.com/emails", json=payload, timeout=20,
                              headers={"Authorization": f"Bearer {os.getenv('RESEND_API_KEY')}"})
            if r.status_code >= 300:
                return False, f"Email service error {r.status_code}: {r.text[:160]}"
            return True, f"Sent to {len(to)} recipient(s)"

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER")
        msg["To"] = ", ".join(to)
        msg.set_content(text or "This message is best viewed in an email client that shows HTML.")
        msg.add_alternative(html, subtype="html")
        if attachment:
            msg.add_attachment(attachment[1], maintype="text", subtype=attachment[2], filename=attachment[0])
        host, port = os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587"))
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as s:
                s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls(context=ctx)
                s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
                s.send_message(msg)
        return True, f"Sent to {len(to)} recipient(s)"
    except Exception as e:
        return False, f"Email failed: {str(e)[:180]}"
