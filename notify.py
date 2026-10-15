<<<<<<< HEAD
"""Notifications: email (configured in the app or via environment), Teams/Slack webhooks, and a log.

Email settings saved in the app live in DATA_DIR/email.json. Passwords and API keys are encrypted with a key
taken from APP_SECRET (recommended) or, if that is not set, a key file generated in DATA_DIR.
Environment variables (SMTP_* or RESEND_API_KEY) still work when nothing is configured in the app.
=======
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
>>>>>>> a22cad222693080dbc19e5d52e88faed6903fd52
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import smtplib
import ssl
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
<<<<<<< HEAD
import errno
from email.utils import formataddr, parseaddr
from pathlib import Path
=======
from email.utils import parseaddr
>>>>>>> a22cad222693080dbc19e5d52e88faed6903fd52

import requests

BASE = Path(__file__).parent
DATA = Path(os.getenv("DATA_DIR", BASE / "data"))
CFG_PATH = DATA / "email.json"
KEY_PATH = DATA / ".secret.key"
LOG_PATH = DATA / "notifications.jsonl"
_lock = threading.Lock()

PRESETS = {
    "gmail": {"label": "Gmail / Google Workspace", "host": "smtp.gmail.com", "port": 587, "security": "starttls",
              "hint": "Use an App Password (Google Account > Security > App passwords), not your normal password."},
    "office365": {"label": "Outlook / Microsoft 365", "host": "smtp.office365.com", "port": 587, "security": "starttls",
                  "hint": "SMTP AUTH must be enabled for the mailbox in Microsoft 365 admin."},
    "zoho": {"label": "Zoho Mail (India)", "host": "smtp.zoho.in", "port": 587, "security": "starttls",
             "hint": "Use an app-specific password if two-factor sign-in is on."},
    "ses": {"label": "Amazon SES (Mumbai)", "host": "email-smtp.ap-south-1.amazonaws.com", "port": 587, "security": "starttls",
            "hint": "Use SES SMTP credentials, and a verified sender address."},
    "custom": {"label": "Other SMTP server", "host": "", "port": 587, "security": "starttls", "hint": ""},
    "brevo": {"label": "Brevo (email API over HTTPS, free 300/day)", "host": "", "port": 0, "security": "",
              "hint": "Works on Render's free plan, where SMTP is blocked. In Brevo: Senders, Domains & Dedicated IPs > add and verify "
                      "your sender email (a Gmail address works). Then SMTP & API > API keys > generate a key."},
    "resend": {"label": "Resend (email API over HTTPS)", "host": "", "port": 0, "security": "",
               "hint": "Works where SMTP ports are blocked. Verify your sending domain in Resend."},
}


# --------------------------------------------------------------------------
# Secret handling
# --------------------------------------------------------------------------
def _fernet():
    from cryptography.fernet import Fernet
    secret = os.getenv("APP_SECRET")
    if secret:
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    else:
        DATA.mkdir(parents=True, exist_ok=True)
        if not KEY_PATH.exists():
            KEY_PATH.write_bytes(Fernet.generate_key())
            try:
                os.chmod(KEY_PATH, 0o600)
            except Exception:
                pass
        key = KEY_PATH.read_bytes().strip()
    return Fernet(key)


def _enc(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def _dec(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except Exception:
        return ""  # key changed (e.g. APP_SECRET edited): user must re-enter the password


# --------------------------------------------------------------------------
# Email configuration
# --------------------------------------------------------------------------
def _load_raw() -> dict:
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def public_config() -> dict:
    raw = _load_raw()
    env = _env_config()
    src = "app" if raw.get("enabled") and raw.get("provider") else ("env" if env else "none")
    return {
        "enabled": bool(raw.get("enabled")), "provider": raw.get("provider", "gmail"),
        "host": raw.get("host", ""), "port": raw.get("port", 587), "security": raw.get("security", "starttls"),
        "username": raw.get("username", ""), "from_name": raw.get("from_name", "Drishti Alerts"),
        "from_email": raw.get("from_email", ""), "reply_to": raw.get("reply_to", ""),
        "has_password": bool(raw.get("password")), "has_api_key": bool(raw.get("api_key")),
        "secret_unreadable": bool((raw.get("password") and not _dec(raw["password"]))
                                  or (raw.get("api_key") and not _dec(raw["api_key"]))),
        "source": src, "env_method": (env or {}).get("method", ""), "presets": PRESETS,
        "secret_from_env": bool(os.getenv("APP_SECRET")), "updated": raw.get("updated"),
        "ready": bool(_effective()),
    }


def save_config(body: dict) -> dict:
    raw = _load_raw()
    provider = body.get("provider") if body.get("provider") in PRESETS else "custom"
    email_re = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    from_email = str(body.get("from_email", "")).strip()
    reply_to = str(body.get("reply_to", "")).strip()
    raw.update(
        enabled=bool(body.get("enabled", True)), provider=provider,
        host=str(body.get("host", "")).strip()[:200], port=int(body.get("port") or 587),
        security=body.get("security") if body.get("security") in ("starttls", "ssl", "none") else "starttls",
        username=str(body.get("username", "")).strip()[:200],
        from_name=str(body.get("from_name", "")).strip()[:80],
        from_email=from_email if re.match(email_re, from_email) else "",
        reply_to=reply_to if re.match(email_re, reply_to) else "",
        updated=datetime.now(timezone.utc).isoformat(),
    )
    if body.get("password"):  # blank means "keep the saved one"
        pw = str(body["password"])
        if provider == "gmail" or "gmail.com" in raw.get("host", ""):
            pw = re.sub(r"\s+", "", pw)  # Google shows app passwords in groups of four
        raw["password"] = _enc(pw)
    if body.get("api_key"):
        raw["api_key"] = _enc(str(body["api_key"]))
    if body.get("clear_secrets"):
        raw["password"], raw["api_key"] = "", ""
    with _lock:
        DATA.mkdir(parents=True, exist_ok=True)
        CFG_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        try:
            os.chmod(CFG_PATH, 0o600)
        except Exception:
            pass
    return public_config()


def _env_config() -> dict | None:
    if os.getenv("BREVO_API_KEY"):
        return {"method": "brevo", "api_key": os.getenv("BREVO_API_KEY"),
                "sender": os.getenv("MAIL_FROM") or os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or ""}
    if os.getenv("RESEND_API_KEY"):
        return {"method": "resend", "api_key": os.getenv("RESEND_API_KEY"),
                "sender": os.getenv("MAIL_FROM") or os.getenv("SMTP_FROM") or "Drishti <alerts@resend.dev>"}
    if os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"):
        port = int(os.getenv("SMTP_PORT", "587"))
        return {"method": "smtp", "host": os.getenv("SMTP_HOST"), "port": port,
                "security": "ssl" if port == 465 else "starttls", "username": os.getenv("SMTP_USER"),
                "password": (re.sub(r"\s+", "", os.getenv("SMTP_PASSWORD")) if "gmail" in os.getenv("SMTP_HOST", "")
                             else os.getenv("SMTP_PASSWORD")),
                "sender": os.getenv("SMTP_FROM") or os.getenv("SMTP_USER"),
                "reply_to": ""}
    return None


def _effective() -> dict | None:
    raw = _load_raw()
    if raw.get("enabled") and raw.get("provider"):
        sender = formataddr((raw.get("from_name") or "", raw.get("from_email") or raw.get("username") or ""))
        if raw["provider"] in ("resend", "brevo"):
            key = _dec(raw.get("api_key", ""))
            return {"method": raw["provider"], "api_key": key, "sender": sender, "reply_to": raw.get("reply_to", "")} if key else None
        pw = _dec(raw.get("password", ""))
        if raw.get("host") and raw.get("username") and pw:
            return {"method": "smtp", "host": raw["host"], "port": int(raw.get("port") or 587),
                    "security": raw.get("security", "starttls"), "username": raw["username"], "password": pw,
                    "sender": sender, "reply_to": raw.get("reply_to", "")}
        return None
    return _env_config()


def _env(name: str, default: str = "") -> str:
    """Read an environment variable and trim surrounding whitespace."""
    return os.getenv(name, default).strip()


def _smtp_password() -> str:
    """Normalize an App Password, accepting spaces between characters."""
    raw = os.getenv("SMTP_PASSWORD", "")
    return re.sub(r"\s+", "", raw)


def method() -> str:
<<<<<<< HEAD
    eff = _effective()
    return eff["method"] if eff else ""


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------
def log(entry: dict) -> None:
    entry = {"at": datetime.now(timezone.utc).isoformat(), **entry}
    with _lock:
        DATA.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_log(limit: int = 100) -> list[dict]:
    if not LOG_PATH.exists():
        return []
    lines = LOG_PATH.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def send(to: list[str], subject: str, html: str, text: str = "", attachment: tuple | None = None,
         category: str = "email") -> tuple[bool, str]:
    """attachment: (filename, bytes, subtype). Returns (ok, message)."""
    to = sorted({t.strip() for t in to if t and re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", t.strip())})
    ok, msg = _send(to, subject, html, text, attachment)
    log({"channel": "email", "category": category, "to": to, "subject": subject, "ok": ok, "message": msg})
    return ok, msg


def _send(to, subject, html, text, attachment):
    if not to:
        return False, "No recipients configured"
    eff = _effective()
    if not eff:
        return False, "Email is not configured (Setup: Email and notifications)"
    try:
        if eff["method"] == "brevo":
            name, addr = parseaddr(eff["sender"])
            if not addr:
                return False, "Set a sender email (it must be verified in Brevo)"
            payload = {"sender": {"name": name or "Drishti Alerts", "email": addr},
                       "to": [{"email": t} for t in to], "subject": subject, "htmlContent": html}
            if text:
                payload["textContent"] = text
            if eff.get("reply_to"):
                payload["replyTo"] = {"email": eff["reply_to"]}
            if attachment:
                payload["attachment"] = [{"name": attachment[0], "content": base64.b64encode(attachment[1]).decode()}]
            r = requests.post("https://api.brevo.com/v3/smtp/email", json=payload, timeout=20,
                              headers={"api-key": eff["api_key"], "accept": "application/json"})
            if r.status_code >= 300:
                detail = r.text[:200]
                if "sender" in detail.lower() and "valid" in detail.lower():
                    detail = "The sender email is not verified in Brevo yet (Senders, Domains & Dedicated IPs)."
                return False, f"Brevo error {r.status_code}: {detail}"
            return True, f"Sent to {len(to)} recipient(s)"
        if eff["method"] == "resend":
            payload = {"from": eff["sender"], "to": to, "subject": subject, "html": html}
            if text:
                payload["text"] = text
            if eff.get("reply_to"):
                payload["reply_to"] = eff["reply_to"]
            if attachment:
                payload["attachments"] = [{"filename": attachment[0], "content": base64.b64encode(attachment[1]).decode()}]
            r = requests.post("https://api.resend.com/emails", json=payload, timeout=20,
                              headers={"Authorization": f"Bearer {eff['api_key']}"})
            if r.status_code >= 300:
                return False, f"Email service error {r.status_code}: {r.text[:160]}"
            return True, f"Sent to {len(to)} recipient(s)"

        m = EmailMessage()
        m["Subject"], m["From"], m["To"] = subject, eff["sender"], ", ".join(to)
        if eff.get("reply_to"):
            m["Reply-To"] = eff["reply_to"]
        m.set_content(text or "This message is best viewed in an email client that shows HTML.")
        m.add_alternative(html, subtype="html")
        if attachment:
            m.add_attachment(attachment[1], maintype="text", subtype=attachment[2], filename=attachment[0])
        ctx = ssl.create_default_context()
        if eff["security"] == "ssl":
            server = smtplib.SMTP_SSL(eff["host"], eff["port"], context=ctx, timeout=25)
        else:
            server = smtplib.SMTP(eff["host"], eff["port"], timeout=25)
        with server as s:
            if eff["security"] == "starttls":
                s.starttls(context=ctx)
            s.login(eff["username"], eff["password"])
            s.send_message(m)
        return True, f"Sent to {len(to)} recipient(s)"
    except smtplib.SMTPAuthenticationError:
        return False, "Sign-in rejected by the mail server. Check the username and password (Gmail needs an App Password)."
    except OSError as e:
        if getattr(e, "errno", None) in (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ECONNREFUSED, errno.ETIMEDOUT) \
                or "timed out" in str(e).lower():
            return False, ("The server cannot open a connection to the mail server. The hosting provider is most likely "
                           "blocking email ports (Render's free plan blocks SMTP). Use Brevo or Resend, which send over HTTPS.")
        return False, f"Could not reach the mail server: {str(e)[:160]}"
    except smtplib.SMTPException as e:
        return False, f"The mail server refused the message: {str(e)[:160]}"
    except Exception as e:
        return False, f"Email failed: {str(e)[:180]}"


# --------------------------------------------------------------------------
# Teams / Slack / generic webhooks
# --------------------------------------------------------------------------
def webhook(hook: dict, title: str, lines: list[str], link: str = "", category: str = "alert",
            data: dict | None = None) -> tuple[bool, str]:
    kind, url = hook.get("type", "generic"), hook.get("url", "")
    try:
        if kind == "teams":  # Teams "Workflows" webhook (Power Automate) with an Adaptive Card
            body = [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True}]
            body += [{"type": "TextBlock", "text": ln, "wrap": True, "spacing": "Small"} for ln in lines]
            card = {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "version": "1.4", "body": body}
            if link:
                card["actions"] = [{"type": "Action.OpenUrl", "title": "Open in Drishti", "url": link}]
            payload = {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive",
                                                           "content": card}]}
        elif kind == "slack":
            text = f"*{title}*\n" + "\n".join(lines) + (f"\n<{link}|Open in Drishti>" if link else "")
            payload = {"text": text}
        else:
            payload = {"title": title, "lines": lines, "link": link, "category": category, "data": data or {}}
        r = requests.post(url, json=payload, timeout=15)
        ok = r.status_code < 300
        msg = "Delivered" if ok else f"HTTP {r.status_code}: {r.text[:120]}"
    except Exception as e:
        ok, msg = False, str(e)[:160]
    log({"channel": kind, "category": category, "to": [hook.get("name") or kind], "subject": title, "ok": ok, "message": msg})
    return ok, msg


# --------------------------------------------------------------------------
# Network check, run from the server itself
# --------------------------------------------------------------------------
def diagnose() -> list[dict]:
    """Can THIS server reach the mail services? (A test on your own PC says nothing about the host's network.)"""
    import socket
    raw = _load_raw()
    targets = [("Gmail SMTP (SSL)", "smtp.gmail.com", 465), ("Gmail SMTP (STARTTLS)", "smtp.gmail.com", 587),
               ("Brevo API (HTTPS)", "api.brevo.com", 443), ("Resend API (HTTPS)", "api.resend.com", 443)]
    if raw.get("host") and raw.get("provider") not in ("gmail", "resend", "brevo"):
        targets.insert(0, (f"Your SMTP server ({raw['host']})", raw["host"], int(raw.get("port") or 587)))
    out = []
    for name, host, port in targets:
        try:
            with socket.create_connection((host, port), timeout=6):
                out.append({"name": name, "target": f"{host}:{port}", "ok": True, "message": "Reachable"})
        except OSError as e:
            blocked = getattr(e, "errno", None) in (errno.ENETUNREACH, errno.EHOSTUNREACH) or "timed out" in str(e).lower()
            out.append({"name": name, "target": f"{host}:{port}", "ok": False,
                        "message": "Blocked by the host's network" if blocked else str(e)[:120]})
    return out
=======
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
>>>>>>> a22cad222693080dbc19e5d52e88faed6903fd52
