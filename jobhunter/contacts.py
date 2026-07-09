"""Stage 14-15: contact discovery via a legitimate provider waterfall.

Order: Apollo (rotating accounts) -> Hunter -> company site (stub).
NEVER returns a guessed/unverified email as sendable — verification_status is recorded
and the send-validator enforces verified-only.

Apollo rotation (BLUEPRINT §18): a pool of accounts, each with an API key. On quota/429
the pool advances to the next key and continues from the same lookup (no re-search).
This is Case A (API-key rotation) — headless-friendly. Rotating free accounts to evade
quotas is a ToS risk the user explicitly accepted; the waterfall degrades to Hunter /
company-site when Apollo is exhausted or blocked.

All providers no-op cleanly when their credentials are absent.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import httpx

from .config import ROOT, load_yaml
from .db import DB

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Prioritized target titles (best contact first) — BLUEPRINT §5 / AGENT_SPEC Stage 14.
TARGET_TITLES = [
    "recruiter", "technical recruiter", "talent acquisition",
    "hiring manager", "engineering manager", "ai lead", "ml lead",
    "head of engineering", "engineering director", "vp engineering",
    "cto", "co-founder", "founder",
]


def _valid_syntax(email: str) -> bool:
    return bool(email and _EMAIL_RE.match(email) and "not_unlocked" not in email)


def _map_apollo_status(s: str | None) -> str:
    return {"verified": "verified", "likely to engage": "verified"}.get(
        (s or "").lower(), "risky" if s else "unknown")


def _map_hunter_status(s: str | None) -> str:
    return {"deliverable": "verified", "risky": "risky", "undeliverable": "risky"}.get(
        (s or "").lower(), "unknown")


# --------------------------------------------------------------------------
class ApolloRotatingProvider:
    name = "apollo"
    BASE = "https://api.apollo.io/api/v1"

    def __init__(self, accounts: list[dict]):
        # only accounts that actually have an API key are usable in headless mode (Case A)
        self.accounts = [a for a in accounts if a.get("api_key")]
        self.idx = 0

    def _key(self) -> Optional[dict]:
        return self.accounts[self.idx] if self.idx < len(self.accounts) else None

    def _advance(self):
        self.idx += 1

    def find(self, domain: str, titles: list[str]) -> Optional[dict]:
        while self._key():
            acct = self._key()
            try:
                person = self._search(acct["api_key"], domain, titles)
                if person is None:
                    return None  # searched fine, just nobody matched
                email = self._reveal(acct["api_key"], person.get("id"))
                if not _valid_syntax(email):
                    return None
                return {
                    "company": domain, "full_name": person.get("name"),
                    "title": person.get("title"), "linkedin_url": person.get("linkedin_url"),
                    "email": email, "source_provider": f"apollo:{acct.get('label')}",
                    "confidence": 0.9,
                    "verification_status": _map_apollo_status(person.get("email_status")),
                }
            except _QuotaExhausted:
                self._advance()  # rotate to next account, continue
                continue
            except Exception:
                return None
        return None  # all accounts exhausted

    def _search(self, api_key: str, domain: str, titles: list[str]) -> Optional[dict]:
        r = httpx.post(f"{self.BASE}/mixed_people/search",
                       headers={"X-Api-Key": api_key, "Content-Type": "application/json",
                                "Cache-Control": "no-cache"},
                       json={"q_organization_domains": domain, "person_titles": titles,
                             "page": 1, "per_page": 10}, timeout=25)
        if r.status_code == 429:
            raise _QuotaExhausted()
        r.raise_for_status()
        people = r.json().get("people", [])
        return people[0] if people else None

    def _reveal(self, api_key: str, person_id: str) -> Optional[str]:
        r = httpx.post(f"{self.BASE}/people/match",
                       headers={"X-Api-Key": api_key, "Content-Type": "application/json",
                                "Cache-Control": "no-cache"},
                       json={"id": person_id}, timeout=25)
        if r.status_code == 429:
            raise _QuotaExhausted()
        r.raise_for_status()
        return (r.json().get("person") or {}).get("email")


class HunterProvider:
    name = "hunter"
    BASE = "https://api.hunter.io/v2"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def find(self, domain: str, titles: list[str]) -> Optional[dict]:
        try:
            r = httpx.get(f"{self.BASE}/domain-search",
                          params={"domain": domain, "api_key": self.api_key, "limit": 10},
                          timeout=25)
            r.raise_for_status()
            emails = r.json().get("data", {}).get("emails", [])
            best = _pick_by_title(emails, titles)
            if not best or not _valid_syntax(best.get("value", "")):
                return None
            verify = self._verify(best["value"])
            return {
                "company": domain,
                "full_name": " ".join(filter(None, [best.get("first_name"), best.get("last_name")])),
                "title": best.get("position"), "linkedin_url": best.get("linkedin"),
                "email": best["value"], "source_provider": "hunter",
                "confidence": (best.get("confidence") or 0) / 100.0,
                "verification_status": verify,
            }
        except Exception:
            return None

    def _verify(self, email: str) -> str:
        try:
            r = httpx.get(f"{self.BASE}/email-verifier",
                          params={"email": email, "api_key": self.api_key}, timeout=25)
            r.raise_for_status()
            return _map_hunter_status(r.json().get("data", {}).get("status"))
        except Exception:
            return "unknown"


class CompanySiteProvider:
    """Honest stub: real team-page parsing is fragile and per-site. Left disabled;
    returns None so the waterfall reports 'not found' rather than a guess."""
    name = "company_site"

    def find(self, domain: str, titles: list[str]) -> Optional[dict]:
        return None


class _QuotaExhausted(Exception):
    pass


def _pick_by_title(items: list[dict], titles: list[str]) -> Optional[dict]:
    for want in titles:
        for it in items:
            pos = (it.get("position") or "").lower()
            if want in pos:
                return it
    return items[0] if items else None


# --------------------------------------------------------------------------
def _load_providers() -> list:
    """Providers come from env vars first (cloud routine), then the local secrets file.

    Env:  APOLLO_API_KEYS="key1,key2"   HUNTER_API_KEY="..."
    File: secrets/apollo_accounts.yaml  (local dev)
    """
    import os
    providers = []
    accts: list[dict] = []
    hunter_key = os.environ.get("HUNTER_API_KEY", "")

    env_keys = [k.strip() for k in os.environ.get("APOLLO_API_KEYS", "").split(",") if k.strip()]
    for i, k in enumerate(env_keys, 1):
        accts.append({"label": f"env{i}", "api_key": k, "monthly_cap": None})

    sec = ROOT / "secrets" / "apollo_accounts.yaml"
    if sec.exists():
        cfg = load_yaml("secrets/apollo_accounts.yaml")
        accts += cfg.get("accounts", [])
        fb = cfg.get("fallback", {})
        hunter_key = hunter_key or fb.get("hunter_api_key", "")

    if any(a.get("api_key") for a in accts):
        providers.append(ApolloRotatingProvider(accts))
    if hunter_key:
        providers.append(HunterProvider(hunter_key))
    return providers


def discover_contact(db: DB, company: str, domain: str) -> Optional[dict]:
    """Run the waterfall; store and return the best contact, or None."""
    if not domain:
        return None
    for prov in _load_providers():
        contact = prov.find(domain, TARGET_TITLES)
        if contact:
            contact["company"] = company  # store under display name, not raw domain
            cid = db.save_contact(contact)
            contact["id"] = cid
            db.log("contacts", "company", company, "found",
                   {"provider": contact["source_provider"], "status": contact["verification_status"]})
            return contact
    db.log("contacts", "company", company, "not_found", {"domain": domain})
    return None


def providers_configured() -> bool:
    return bool(_load_providers())
