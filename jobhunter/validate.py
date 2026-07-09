"""Stage 18: pre-send validation — the guardrails, enforced in CODE (BLUEPRINT §15).

Every check must pass or the email drops to the review queue instead of sending.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from .config import load_yaml
from .db import DB

# EU/UK/CA location hints for the region gate (coarse but safe).
_REGION_HINTS = {
    "EU": ["germany", "france", "spain", "italy", "netherlands", "ireland", "poland",
           "sweden", "portugal", "belgium", "austria", "denmark", "finland", "eu remote"],
    "UK": ["united kingdom", "london", "manchester", "uk", "england", "scotland"],
    "CA": ["canada", "toronto", "vancouver", "montreal", "ontario"],
}


@dataclass
class Verdict:
    ok: bool
    reasons: list = field(default_factory=list)

    def fail(self, r: str):
        self.ok = False
        self.reasons.append(r)


def load_rules() -> dict:
    try:
        return load_yaml("config/outreach_rules.yaml")
    except FileNotFoundError:
        return {}


def check_email_alive(url: str) -> bool:
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
        return r.status_code < 400
    except Exception:
        return True  # network hiccup shouldn't block; don't false-expire a live job


def validate(db: DB, rules: dict, job, contact: dict, draft: dict) -> Verdict:
    v = Verdict(ok=True)
    email = (contact.get("email") or "").lower()

    # 1. verified OR high-confidence email (user policy)
    if rules.get("verified_email_only", True):
        status = contact.get("verification_status")
        conf = float(contact.get("confidence") or 0)
        min_conf = float(rules.get("min_confidence", 0.9))
        ok = (status == "verified") or (rules.get("allow_high_confidence", True) and conf >= min_conf)
        if not ok:
            v.fail(f"email not verified/high-confidence (status={status}, conf={conf:.2f})")

    # 2. suppression / blocklist
    if db.is_suppressed(email):
        v.fail("email suppressed")
    dom = email.split("@")[-1] if "@" in email else ""
    if dom in [d.lower() for d in (rules.get("blocklist_domains") or [])]:
        v.fail("domain blocklisted")
    if job.company.lower() in [c.lower() for c in (rules.get("blocklist_companies") or [])]:
        v.fail("company blocklisted")

    # 3. dedup — never a second email to same contact+job
    if contact.get("id") and db.email_exists(contact["id"], job.content_hash()):
        v.fail("already emailed this contact for this job")

    # 4. per-company cap
    cap = int(rules.get("per_company_cap", 1))
    if db.company_email_count_today(job.company) >= cap:
        v.fail(f"per-company cap reached ({cap})")

    # 5. daily cap
    dcap = int(rules.get("daily_send_cap", 15))
    if db.emails_sent_today() >= dcap:
        v.fail(f"daily send cap reached ({dcap})")

    # 6. region gate
    loc = (job.location or "").lower()
    for region in (rules.get("exclude_regions") or []):
        if any(h in loc for h in _REGION_HINTS.get(region, [])):
            v.fail(f"excluded region ({region})")
            break

    # 7. draft sanity — no placeholders
    body = (draft.get("body") or "")
    if re.search(r"\[(company|name|role|todo)\]|XXXX|TODO", body, re.I):
        v.fail("draft has unfilled placeholders")
    if not draft.get("subject") or not body.strip():
        v.fail("empty subject or body")

    # 8. job still live
    if job.url and not check_email_alive(job.url):
        v.fail("job posting no longer reachable")

    # 9. interview-probability floor — below it, queue for manual review instead of sending
    min_prob = float(rules.get("min_interview_prob_to_send", 0) or 0)
    if min_prob:
        prob = db.get_interview_prob(job.content_hash())
        if prob is not None and prob < min_prob:
            v.fail(f"interview probability {prob:.2f} below send floor {min_prob:.2f}")

    return v
