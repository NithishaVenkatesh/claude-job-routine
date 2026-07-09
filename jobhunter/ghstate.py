"""Persistent state via the GitHub Contents API — for environments where every host
except api.github.com is firewalled (the Claude cloud sandbox).

State = one JSON file (state/state.json) in the repo:
  seen:      [{hash, company, title, status}]      -> prevents re-scoring/re-considering
  contacts:  [contact rows]                        -> known contacts
  emails:    [email rows incl. body]               -> queue + sent history (dedup!)
  suppression: [{email, reason}]

pull_state(db): fetch JSON -> seed the (fresh, local SQLite) DB.
push_state(db): dump DB -> commit JSON back to the repo.

Env: GITHUB_TOKEN (contents:write), optional GITHUB_STATE_REPO (owner/repo).
"""
from __future__ import annotations

import base64
import json
import os

import httpx

from .db import DB

# State lives in a PRIVATE repo — it contains contacts' emails and outreach bodies,
# which must never land in the public code repo.
REPO = os.environ.get("GITHUB_STATE_REPO", "NithishaVenkatesh/claude-job-state")
PATH = "state/state.json"
API = f"https://api.github.com/repos/{REPO}/contents/{PATH}"


def _headers():
    tok = os.environ.get("GITHUB_TOKEN", "")
    if not tok:
        raise RuntimeError("GITHUB_TOKEN not set — cannot sync state")
    return {"Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _fetch() -> tuple[dict | None, str | None]:
    """Returns (state_dict, blob_sha) or (None, None) if the file doesn't exist yet."""
    r = httpx.get(API, headers=_headers(), timeout=30)
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    data = r.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return json.loads(content), data["sha"]


def pull_state(db: DB) -> dict:
    """Seed a fresh DB from the repo's state file. Idempotent."""
    state, _ = _fetch()
    counts = {"seen": 0, "contacts": 0, "emails": 0, "suppression": 0}
    if not state:
        return counts

    now = "1970-01-01T00:00:00+00:00"
    for j in state.get("seen", []):
        try:
            db.s.execute(
                """INSERT INTO jobs (content_hash, source, source_company, external_id,
                     title, company, url, location, remote, employment_type, description,
                     posted_at, first_seen, last_seen, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                (j["hash"], "state", "state", j["hash"][:16], j.get("title", ""),
                 j.get("company", ""), "", "", 0, "", "", j.get("posted_at"), now, now,
                 j.get("status", "seen_prior")))
            # a score row marks it as already-processed so it's never re-scored
            db.s.execute(
                """INSERT INTO job_scores (content_hash, score, interview_prob, model, scored_at)
                   VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING""",
                (j["hash"], 0, 0, "state_restore", now))
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


def push_state(db: DB, message: str = "routine: update state") -> int:
    """Dump the DB's durable facts into state/state.json in the repo."""
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

    state = {"version": 1, "seen": seen, "contacts": contacts,
             "emails": emails, "suppression": suppression}
    payload = json.dumps(state, indent=1)

    _, sha = _fetch()
    body = {"message": message,
            "content": base64.b64encode(payload.encode()).decode()}
    if sha:
        body["sha"] = sha
    r = httpx.put(API, headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    return len(payload)
