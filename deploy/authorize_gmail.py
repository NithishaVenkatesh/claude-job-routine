#!/usr/bin/env python3
"""One-time Gmail authorization. Run interactively once to mint secrets/gmail_token.json,
which the routine then reuses headlessly (auto-refreshing).

Prereq:
  1. Create an OAuth client (Desktop app) in Google Cloud Console, enable the Gmail API.
  2. Download the client secret JSON to secrets/gmail_client_secret.json.
  3. Run:  ./.venv/bin/python deploy/authorize_gmail.py

This opens a browser for consent. Nothing sends here — it only stores the token.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
CLIENT = ROOT / "secrets" / "gmail_client_secret.json"
TOKEN = ROOT / "secrets" / "gmail_token.json"


def main() -> int:
    if not CLIENT.exists():
        print(f"Missing {CLIENT}. Download an OAuth Desktop client secret there first.")
        return 1
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Install deps first: ./.venv/bin/python -m pip install "
              "google-auth-oauthlib google-api-python-client")
        return 1
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN.write_text(creds.to_json())
    print(f"Saved {TOKEN}. Gmail sending is now authorized (still gated by autosend_enabled).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
