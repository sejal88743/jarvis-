"""
email_sender.py — send and read email through any SMTP/IMAP account.

Why this exists
    The assistant could send WhatsApp messages but had no email at all. Email
    needs three things WhatsApp does not: an account, an app password (Gmail
    refuses the login password), and attachments — "email karna, CV attach
    kar na" is the whole request, so attachment support is not an extra.

How it is configured
    Everything lives in config/api_keys.json under "email", editable by hand
    or through the settings UI — no code change, nothing leaves the machine
    except the mail itself:

        "email": {
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "imap_host": "imap.gmail.com",
            "imap_port": 993,
            "address":   "you@gmail.com",
            "password":  "your-16-character app password"
        }

    Gmail: enable 2-step verification, then generate an App Password at
    myaccount.google.com/apppasswords — the normal password will NOT work.
    Outlook: smtp.office365.com:587 / outlook.office365.com:993.

Actions
    send       — to, subject, body, and any attachment (CV, invoice, anything)
    read       — the newest unread mail, or the newest N messages
    search     — mail matching a Gmail-style IMAP search string
"""
from __future__ import annotations

import json
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent

_PRESETS = {
    "gmail":   ("smtp.gmail.com", 587, "imap.gmail.com", 993),
    "outlook": ("smtp.office365.com", 587, "outlook.office365.com", 993),
    "yahoo":   ("smtp.mail.yahoo.com", 587, "imap.mail.yahoo.com", 993),
}


def _email_cfg() -> dict:
    try:
        data = json.loads((_BASE / "config" / "api_keys.json")
                          .read_text(encoding="utf-8"))
        cfg = data.get("email")
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _resolve_hosts(cfg: dict) -> dict:
    if not cfg.get("smtp_host") and cfg.get("provider"):
        hosts = _PRESETS.get(str(cfg.get("provider")).lower().strip())
        if hosts:
            cfg = {**cfg, "smtp_host": hosts[0], "smtp_port": hosts[1],
                   "imap_host": hosts[2], "imap_port": hosts[3]}
    return cfg


def _configured(cfg: dict) -> str:
    missing = [k for k in ("address", "password") if not cfg.get(k)]
    if missing:
        return ("Email configured nahi hai. config/api_keys.json mein 'email' "
                "section add karein — address, password (Gmail ke liye App "
                "Password), aur provider ('gmail' ya 'outlook').")
    if not cfg.get("smtp_host"):
        return ("SMTP/IMAP host nahi mila. 'provider': 'gmail' ya 'outlook' "
                "add karein, ya smtp_host/imap_host khud likhein.")
    return ""


# ── send ─────────────────────────────────────────────────────────────────────

def send_email(cfg: dict, to: str, subject: str, body: str,
               attachments: list[str]) -> str:
    msg = EmailMessage()
    msg["From"] = cfg["address"]
    msg["To"] = to
    msg["Subject"] = subject or "(no subject)"
    msg.set_content(body or "")

    for att in attachments:
        p = Path(str(att).strip().strip('"'))
        if not p.exists():
            return f"Attachment nahi mila: {p} — email bheja nahi."
        ctype, _ = mimetypes_guess(p.suffix)
        maintype, subtype = ctype.split("/", 1)
        if maintype == "text":
            msg.add_attachment(p.read_bytes(), maintype=maintype,
                               subtype=subtype, filename=p.name)
        else:
            msg.add_attachment(p.read_bytes(), maintype=maintype,
                               subtype=subtype, filename=p.name)

    host = cfg["smtp_host"]
    port = int(cfg.get("smtp_port") or 587)
    if port == 465:
        server = smtplib.SMTP_SSL(host, port,
                                  context=ssl.create_default_context(),
                                  timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.starttls(context=ssl.create_default_context())
    try:
        try:
            server.login(cfg["address"], cfg["password"])
            server.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            return ("Email bhej nahi paya — login fail hua. Gmail ho to normal "
                    "password kaam NAHI karta: Google Account → Security → "
                    "2-Step Verification ON, phir myaccount.google.com/"
                    "apppasswords se 16-character App Password banao.")
        except smtplib.SMTPServerDisconnected:
            return ("Email bhej nahi paya — server ne connection tod diya. "
                    "Aksar yeh galat password ya kamzor internet par hota hai. "
                    "App Password check karein aur dobara try karein.")
        except (smtplib.SMTPException, OSError) as e:
            return f"Email bhej nahi paya: {e}"
    finally:
        try:
            server.quit()
        except Exception:
            pass

    att_names = ", ".join(Path(a).name for a in attachments) if attachments else ""
    return (f"Email {to} ko bhej diya. Subject: {subject or '(no subject)'}"
            f"{' — attachment: ' + att_names if att_names else ''}.")


def mimetypes_guess(suffix: str) -> tuple[str, str]:
    import mimetypes
    ctype, _ = mimetypes.guess_type("x" + (suffix or ""))
    return (ctype or "application/octet-stream"), ""


# ── read / search ────────────────────────────────────────────────────────────

def read_emails(cfg: dict, count: int = 5, unread_only: bool = False,
                search: str = "") -> str:
    from imaplib import IMAP4, IMAP4_SSL

    host = cfg["imap_host"]
    port = int(cfg.get("imap_port") or 993)
    conn = (IMAP4_SSL(host, port) if port == 993
            else IMAP4(host, port))
    try:
        conn.login(cfg["address"], cfg["password"])
        conn.select("INBOX", readonly=True)
        if search:
            status, data = conn.search(None, search)
        elif unread_only:
            status, data = conn.search(None, "UNSEEN")
        else:
            status, data = conn.search(None, "ALL")
        ids = (data[0] or b"").split()
        if not ids:
            return ("Koi mail nahi mila" +
                    (" us search par" if search else "") + ".")
        latest = ids[-count:][::-1]
        lines = []
        for i in latest:
            status, msg_data = conn.fetch(i, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            from email import message_from_bytes
            m = message_from_bytes(msg_data[0][1])
            frm = (m.get("From") or "").strip()
            subj = (m.get("Subject") or "(no subject)").strip()
            try:
                when = parsedate_to_datetime(m.get("Date")).strftime("%d %b %H:%M")
            except Exception:
                when = "?"
            body = _body_text(m)
            lines.append(f"[{when}] {frm} — {subj}\n  {body[:160]}")
        return "\n".join(lines) if lines else "Mail padh nahi paya."
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _body_text(m) -> str:
    if m.is_multipart():
        for part in m.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "replace")
                except Exception:
                    continue
        return ""
    try:
        return m.get_payload(decode=True).decode(
            m.get_content_charset() or "utf-8", "replace")
    except Exception:
        return str(m.get_payload())[:200]


# ── tool entry point ─────────────────────────────────────────────────────────

def send_and_read_email(parameters: dict, player=None) -> str:
    p = parameters or {}
    cfg = _resolve_hosts(_email_cfg())
    action = str(p.get("action") or "send").strip().lower()

    fail = _configured(cfg)
    if fail:
        return fail

    try:
        if action == "send":
            to = str(p.get("to") or "").strip()
            if not to or "@" not in to:
                return "Email address chahiye — kis ko bhejna hai?"
            subject = str(p.get("subject") or "").strip()
            body = str(p.get("body") or "").strip()
            atts = p.get("attachments") or []
            if isinstance(atts, str):
                atts = [a for a in re.split(r"[;,]", atts) if a.strip()]
            return send_email(cfg, to, subject, body, atts)

        if action in ("read", "search"):
            count = max(1, min(20, int(p.get("count") or 5)))
            return read_emails(cfg, count=count,
                               unread_only=bool(p.get("unread_only")),
                               search=str(p.get("search_query") or "").strip())

        return f"email_sender: unknown action '{action}' (send | read | search)"
    except Exception as e:
        print(f"[EmailSender] ❌ {action}: {e}")
        return f"Email '{action}' fail hua: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ─────────────

TOOL = {
    "name": "email_sender",
    "description": (
        "Send and read EMAIL through the user's configured SMTP/IMAP account. "
        "Use action='send' with to, subject, body and optional attachments — "
        "an attachment is any file path, e.g. the user's CV, an invoice, a "
        "screenshot; when the user says 'CV attach karke bhejo', attach the "
        "CV file they named. Use action='read' for the newest unread mail, or "
        "action='search' with a search_query in IMAP/Gmail syntax such as "
        "'FROM \"hr@company.com\"' or 'UNSEEN'. Say out loud what you are "
        "sending before you send it — email reaches the outside world."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "send | read | search",
            },
            "to": {
                "type": "STRING",
                "description": "Recipient email address (send).",
            },
            "subject": {
                "type": "STRING",
                "description": "Subject line (send).",
            },
            "body": {
                "type": "STRING",
                "description": "The email body text (send).",
            },
            "attachments": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "File paths to attach (send), e.g. the CV. Optional.",
            },
            "count": {
                "type": "INTEGER",
                "description": "How many emails to read (default 5).",
            },
            "unread_only": {
                "type": "BOOLEAN",
                "description": "Only unread mail when reading.",
            },
            "search_query": {
                "type": "STRING",
                "description": "IMAP search string for action='search', e.g. 'FROM \"x@y.com\"'.",
            },
        },
        "required": ["action"],
    },
    "handler": send_and_read_email,
}
