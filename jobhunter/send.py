"""Stage 19: sending — guarded.

Two sender backends, whichever is configured:
  1. SMTP with a Gmail App Password (simplest): set GMAIL_SENDER + GMAIL_APP_PASSWORD.
  2. Gmail API OAuth: run deploy/authorize_gmail.py to mint secrets/gmail_token.json.

Shadow mode (default): autosend_enabled=false → emails are queued, nothing is transmitted.
A send happens only when autosend_enabled=true AND a sender is configured AND all guardrails pass.
"""
from __future__ import annotations

import base64
import os
import smtplib
import ssl
from email.mime.text import MIMEText
from pathlib import Path

from .config import ROOT

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
TOKEN_PATH = ROOT / "secrets" / "gmail_token.json"


# --- SMTP (Gmail App Password) --------------------------------------------
def smtp_ready() -> bool:
    return bool(os.environ.get("GMAIL_SENDER") and os.environ.get("GMAIL_APP_PASSWORD"))


def send_smtp(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    sender = os.environ.get("GMAIL_SENDER", "")
    pw = os.environ.get("GMAIL_APP_PASSWORD", "")
    try:
        msg = MIMEText(body)
        msg["From"] = sender
        msg["To"] = to_email
        msg["Subject"] = subject
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
            s.login(sender, pw)
            s.sendmail(sender, [to_email], msg.as_string())
        return True, f"smtp:{sender}->{to_email}"
    except Exception as e:
        return False, str(e)[:300]


def sender_ready() -> bool:
    return smtp_ready() or gmail_ready()


def send_email(to_email: str, subject: str, body: str, from_name: str = "") -> tuple[bool, str]:
    """Unified send: SMTP (App Password) if configured, else Gmail API."""
    if smtp_ready():
        return send_smtp(to_email, subject, body)
    if gmail_ready():
        return send_gmail(to_email, subject, body, from_name)
    return False, "no sender configured (set GMAIL_SENDER+GMAIL_APP_PASSWORD or run authorize_gmail.py)"


def gmail_ready() -> bool:
    if not TOKEN_PATH.exists():
        return False
    try:
        import google.auth  # noqa: F401
        from googleapiclient.discovery import build  # noqa: F401
        return True
    except ImportError:
        return False


def _service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def send_gmail(to_email: str, subject: str, body: str, from_name: str = "") -> tuple[bool, str]:
    """Actually transmit. Returns (ok, message_id_or_error)."""
    try:
        svc = _service()
        msg = MIMEText(body)
        msg["to"] = to_email
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
        return True, sent.get("id", "")
    except Exception as e:
        return False, str(e)[:300]
