"""Notifications: email (configured in the app or via environment), Teams/Slack webhooks, and a log.

Email settings saved in the app live in DATA_DIR/email.json. Passwords and API keys are encrypted with a key
taken from APP_SECRET (recommended) or, if that is not set, a key file generated in DATA_DIR.
Environment variables (SMTP_* or RESEND_API_KEY) still work when nothing is configured in the app.
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
import errno
from email.utils import formataddr, parseaddr
from pathlib import Path

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
        "api_key_hint": raw.get("api_key_hint", "") if raw.get("api_key") else "",
        "api_key_saved": raw.get("api_key_saved") if raw.get("api_key") else None,
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
        key = re.sub(r"\s+", "", str(body["api_key"]))  # copied keys often carry spaces or line breaks
        if provider == "brevo" and key.startswith("xsmtpsib-"):
            raise ValueError("This is a Brevo SMTP key. Use an API key instead: in Brevo open SMTP & API > API keys "
                             "> Generate a new API key. It starts with xkeysib-.")
        raw["api_key"] = _enc(key)
        raw["api_key_hint"] = key[:8] + "..." + key[-4:]
        raw["api_key_saved"] = datetime.now(timezone.utc).isoformat()
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


def method() -> str:
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
            maintype = "application" if attachment[2] == "pdf" else "text"
            m.add_attachment(attachment[1], maintype=maintype, subtype=attachment[2], filename=attachment[0])
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



def verify() -> dict:
    """Ask the email provider whether the saved key works and the sender is verified."""
    eff = _effective()
    if not eff:
        return {"ok": False, "message": "Email is not configured yet."}
    try:
        if eff["method"] == "brevo":
            h = {"api-key": eff["api_key"], "accept": "application/json"}
            r = requests.get("https://api.brevo.com/v3/account", headers=h, timeout=15)
            if r.status_code == 401:
                msg = r.json().get("message", "") if r.headers.get("content-type", "").startswith("application/json") else r.text
                if "ip" in msg.lower():
                    return {"ok": False, "message": "Brevo blocked this server's IP address. In Brevo: Security > Authorised IPs, "
                                                    "turn off IP blocking or add Render's address."}
                return {"ok": False, "message": "Brevo does not recognise this key. Make sure it is an API key (starts with "
                                                "xkeysib-, not xsmtpsib-), copied completely, and not deleted in Brevo."}
            if r.status_code >= 300:
                return {"ok": False, "message": f"Brevo error {r.status_code}: {r.text[:160]}"}
            account = r.json().get("email", "")
            _, sender = parseaddr(eff["sender"])
            sender_ok, sender_msg = None, ""
            rs = requests.get("https://api.brevo.com/v3/senders", headers=h, timeout=15)
            if rs.status_code < 300:
                senders = rs.json().get("senders", [])
                match = [x for x in senders if x.get("email", "").lower() == (sender or "").lower()]
                sender_ok = bool(match and match[0].get("active", True))
                sender_msg = ("Sender verified" if sender_ok else
                              f"{sender or 'The sender email'} is not a verified sender in Brevo. Add it under "
                              "Senders, Domains & Dedicated IPs and click the link Brevo emails you.")
            return {"ok": True, "account": account, "sender_ok": sender_ok,
                    "message": f"Key works (Brevo account {account}). {sender_msg}".strip()}
        if eff["method"] == "resend":
            r = requests.get("https://api.resend.com/domains", timeout=15,
                             headers={"Authorization": f"Bearer {eff['api_key']}"})
            if r.status_code in (401, 403):
                return {"ok": False, "message": "Resend does not recognise this key."}
            return {"ok": r.status_code < 300, "message": "Key works (Resend)." if r.status_code < 300 else r.text[:160]}
        # SMTP: sign in without sending anything
        ctx = ssl.create_default_context()
        server = (smtplib.SMTP_SSL(eff["host"], eff["port"], context=ctx, timeout=15) if eff["security"] == "ssl"
                  else smtplib.SMTP(eff["host"], eff["port"], timeout=15))
        with server as srv:
            if eff["security"] == "starttls":
                srv.starttls(context=ctx)
            srv.login(eff["username"], eff["password"])
        return {"ok": True, "message": f"Signed in to {eff['host']} as {eff['username']}."}
    except smtplib.SMTPAuthenticationError:
        return {"ok": False, "message": "The mail server rejected the username or password (Gmail needs an App Password)."}
    except OSError as e:
        if getattr(e, "errno", None) in (errno.ENETUNREACH, errno.EHOSTUNREACH) or "timed out" in str(e).lower():
            return {"ok": False, "message": "This server cannot reach the mail server (email ports blocked). Use Brevo or Resend."}
        return {"ok": False, "message": f"Connection problem: {str(e)[:160]}"}
    except Exception as e:
        return {"ok": False, "message": f"Check failed: {str(e)[:160]}"}
