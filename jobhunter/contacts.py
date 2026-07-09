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

    def find(self, domain: str, titles: list[str], company: str = "") -> Optional[dict]:
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


_ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
              "myworkdayjobs.com", "smartrecruiters.com", "job-boards")


def _is_ats_host(domain: str) -> bool:
    d = (domain or "").lower()
    return (not d) or any(h in d for h in _ATS_HOSTS)


class HunterProvider:
    """Hunter.io with multi-key rotation: use key #1 until its quota is exhausted,
    then advance to #2, #3 ... (the user's 'switch credential when limit is over')."""
    name = "hunter"
    BASE = "https://api.hunter.io/v2"

    def __init__(self, api_keys: list[str]):
        self.keys = [k for k in api_keys if k]
        self.idx = 0

    def _key(self) -> Optional[str]:
        return self.keys[self.idx] if self.idx < len(self.keys) else None

    def _get(self, path: str, params: dict):
        """GET with key rotation on quota errors. Returns (json, ok) or (None, False)."""
        while self._key() is not None:
            p = {**params, "api_key": self._key()}
            r = httpx.get(f"{self.BASE}/{path}", params=p, timeout=25)
            if r.status_code in (429,) or (r.status_code == 403 and "usage" in r.text.lower()):
                self.idx += 1          # this key is spent — rotate
                continue
            if r.status_code >= 400:
                return None, False
            return r.json(), True
        return None, False             # all keys exhausted

    def find(self, domain: str, titles: list[str], company: str = "") -> Optional[dict]:
        # ATS URLs give the board host, not the real company domain -> search by name
        params = {"limit": 10}
        if _is_ats_host(domain) and company:
            params["company"] = company
        else:
            params["domain"] = domain
        data, ok = self._get("domain-search", params)
        if not ok or not data:
            return None
        emails = data.get("data", {}).get("emails", [])
        best = _pick_by_title(emails, titles)
        if not best or not _valid_syntax(best.get("value", "")):
            return None
        return {
            "company": company or domain,
            "full_name": " ".join(filter(None, [best.get("first_name"), best.get("last_name")])),
            "title": best.get("position"), "linkedin_url": best.get("linkedin"),
            "email": best["value"], "source_provider": "hunter",
            "confidence": (best.get("confidence") or 0) / 100.0,
            "verification_status": self._verify(best["value"]),
        }

    def _verify(self, email: str) -> str:
        data, ok = self._get("email-verifier", {"email": email})
        if not ok or not data:
            return "unknown"
        return _map_hunter_status(data.get("data", {}).get("status"))


class CompanySiteProvider:
    """Honest stub: real team-page parsing is fragile and per-site. Left disabled;
    returns None so the waterfall reports 'not found' rather than a guess."""
    name = "company_site"

    def find(self, domain: str, titles: list[str], company: str = "") -> Optional[dict]:
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

    # Hunter keys (rotate through all of them): HUNTER_API_KEYS="k1,k2,k3" or HUNTER_API_KEY
    hunter_keys = [k.strip() for k in os.environ.get("HUNTER_API_KEYS", "").split(",") if k.strip()]
    single = os.environ.get("HUNTER_API_KEY", "").strip()
    if single:
        hunter_keys.append(single)

    env_keys = [k.strip() for k in os.environ.get("APOLLO_API_KEYS", "").split(",") if k.strip()]
    for i, k in enumerate(env_keys, 1):
        accts.append({"label": f"env{i}", "api_key": k, "monthly_cap": None})

    sec = ROOT / "secrets" / "apollo_accounts.yaml"
    if sec.exists():
        cfg = load_yaml("secrets/apollo_accounts.yaml")
        accts += cfg.get("accounts", [])
        fb = cfg.get("fallback", {})
        for k in ([fb.get("hunter_api_key")] if fb.get("hunter_api_key") else []):
            hunter_keys.append(k)

    if any(a.get("api_key") for a in accts):
        providers.append(ApolloRotatingProvider(accts))
    if hunter_keys:
        providers.append(HunterProvider(hunter_keys))
    return providers


def discover_contact(db: DB, company: str, domain: str) -> Optional[dict]:
    """Run the waterfall; store and return the best contact, or None."""
    if not domain:
        return None
    for prov in _load_providers():
        contact = prov.find(domain, TARGET_TITLES, company)
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
