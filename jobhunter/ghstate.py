"""Persistent state as a single JSON file on disk.

In the locked-down cloud sandbox, the ONLY store the agent can reach is via its
server-side MCP connectors (Google Drive). So the flow is:
  agent downloads state file from Drive -> data/state_in.json
  `state-pull data/state_in.json`   loads it into the local DB
  ... pipeline runs ...
  `state-push data/state_out.json`  dumps the DB to a file
  agent uploads data/state_out.json back to Drive

This module only touches LOCAL files — no network. The agent handles the Drive
transfer with MCP tools. (Locally on a Mac you don't need this at all; Neon/SQLite
is the store there.)

State schema (state.json):
  seen:        [{hash, company, title, status, posted_at}]
  contacts:    [contact rows]
  emails:      [email rows incl. body]     <- the real dedup: who we've contacted
  suppression: [{email, reason}]
"""
from __future__ import annotations

import json
from pathlib import Path

from .db import DB

DEFAULT_IN = "data/state_in.json"
DEFAULT_OUT = "data/state_out.json"
_NULL_TS = "1970-01-01T00:00:00+00:00"


def _serialize(db: DB) -> dict:
    seen = [{"hash": r["content_hash"], "company": r["company"], "title": r["title"],
             "status": r["status"], "posted_at": r["posted_at"]}
            for r in db.all_jobs()]
    contacts = [dict(company=r["company"], full_name=r["full_name"], title=r["title"],
                     linkedin_url=r["linkedin_url"], email=r["email"],
                     source_provider=r["source_provider"], confidence=r["confidence"],
                     verification_status=r["verification_status"])
                for r in db.s.query("SELECT * FROM contacts")]
    emails = [dict(job_hash=r["job_hash"], contact_id=r["contact_id"], company=r["company"],
                   to_email=r["to_email"], template_class=r["template_class"],
                   subject=r["subject"], body=r["body"], hook_note=r["hook_note"],
                   status=r["status"], sent_at=r["sent_at"])
              for r in db.s.query("SELECT * FROM emails")]
    suppression = [dict(email=r["email"], reason=r["reason"])
                   for r in db.s.query("SELECT * FROM suppression")]
    return {"version": 1, "seen": seen, "contacts": contacts,
            "emails": emails, "suppression": suppression}


def _load(db: DB, state: dict) -> dict:
    counts = {"seen": 0, "contacts": 0, "emails": 0, "suppression": 0}
    for j in state.get("seen", []):
        try:
            db.s.execute(
                """INSERT INTO jobs (content_hash, source, source_company, external_id,
                     title, company, url, location, remote, employment_type, description,
                     posted_at, first_seen, last_seen, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                (j["hash"], "state", "state", j["hash"][:16], j.get("title", ""),
                 j.get("company", ""), "", "", 0, "", "", j.get("posted_at"),
                 _NULL_TS, _NULL_TS, j.get("status", "seen_prior")))
            db.s.execute(
                """INSERT INTO job_scores (content_hash, score, interview_prob, model, scored_at)
                   VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING""",
                (j["hash"], 0, 0, "state_restore", _NULL_TS))
            counts["seen"] += 1
        except Exception:
            continue
    for c in state.get("contacts", []):
        db.save_contact({k: c.get(k) for k in
                         ("company", "full_name", "title", "linkedin_url", "email",
                          "source_provider", "confidence", "verification_status")})
        counts["contacts"] += 1
    for e in state.get("emails", []):
        db.save_email({k: e.get(k) for k in
                       ("job_hash", "contact_id", "company", "to_email", "template_class",
                        "subject", "body", "hook_note", "status")})
        counts["emails"] += 1
    for s in state.get("suppression", []):
        db.suppress(s.get("email", ""), s.get("reason", "restored"))
        counts["suppression"] += 1
    return counts


def pull_state(db: DB, path: str = DEFAULT_IN) -> dict:
    """Load state from a local JSON file. No-op (first run) if the file is missing."""
    p = Path(path)
    if not p.exists():
        return {"seen": 0, "contacts": 0, "emails": 0, "suppression": 0, "_note": "no state file yet"}
    try:
        state = json.loads(p.read_text())
    except (ValueError, OSError) as e:
        return {"error": f"could not read {path}: {e}"}
    return _load(db, state)


def push_state(db: DB, path: str = DEFAULT_OUT) -> int:
    """Dump the DB's durable facts to a local JSON file for the agent to upload."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_serialize(db), indent=1)
    p.write_text(payload)
    return len(payload)
