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

# Prioritized target titles, decision-makers FIRST (AGENT_SPEC Stage 14:
# founder -> CTO -> eng leadership -> hiring manager -> recruiter). A founder/eng-lead
# reply converts far better for a junior candidate than a recruiter inbox.
TARGET_TITLES = [
    "founder", "co-founder", "cto",
    "vp engineering", "head of engineering", "engineering director",
    "ai lead", "ml lead", "engineering manager",
    "hiring manager", "technical recruiter", "recruiter", "talent acquisition",
]

# Speculative / funding-trail leads (no public posting yet): the founder/CTO outreach
# IS the play — never dilute it with a recruiter contact.
SPECULATIVE_TITLES = ["founder", "co-founder", "cto"]


def tier_rank(title: str | None) -> int:
    """Client-side ranking of a contact title against TARGET_TITLES. Providers treat
    person_titles as a FILTER, not a priority order — so we must sort ourselves."""
    t = (title or "").lower()
    return next((i for i, want in enumerate(TARGET_TITLES) if want in t), len(TARGET_TITLES))


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
        all_ = self.find_all(domain, titles, company)
        return all_[0] if all_ else None

    def find_all(self, domain: str, titles: list[str], company: str = "") -> list[dict]:
        """Every matched person with a valid revealed email, best tier first.
        Quota exhaustion mid-list rotates to the next account and resumes at the
        same person (no re-search, no skipped candidates)."""
        people: Optional[list] = None
        out: list[dict] = []
        i = 0
        while self._key():
            acct = self._key()
            try:
                if people is None:
                    people = self._search(acct["api_key"], domain, titles)
                    # provider order is arbitrary — rank decision-makers first ourselves
                    people.sort(key=lambda p: tier_rank(p.get("title")))
                while i < len(people):
                    person = people[i]
                    email = self._reveal(acct["api_key"], person.get("id"))
                    i += 1
                    if not _valid_syntax(email):
                        continue
                    out.append({
                        "company": domain, "full_name": person.get("name"),
                        "title": person.get("title"), "linkedin_url": person.get("linkedin_url"),
                        "email": email, "source_provider": f"apollo:{acct.get('label')}",
                        "confidence": 0.9,
                        "verification_status": _map_apollo_status(person.get("email_status")),
                    })
                return out
            except _QuotaExhausted:
                self._advance()  # rotate to next account, resume from person i
                continue
            except Exception:
                return out
        return out  # all accounts exhausted — return whatever was resolved

    def _search(self, api_key: str, domain: str, titles: list[str]) -> list[dict]:
        r = httpx.post(f"{self.BASE}/mixed_people/search",
                       headers={"X-Api-Key": api_key, "Content-Type": "application/json",
                                "Cache-Control": "no-cache"},
                       json={"q_organization_domains": domain, "person_titles": titles,
                             "page": 1, "per_page": 10}, timeout=25)
        if r.status_code == 429:
            raise _QuotaExhausted()
        r.raise_for_status()
        return r.json().get("people", [])

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
        all_ = self.find_all(domain, titles, company)
        return all_[0] if all_ else None

    def find_all(self, domain: str, titles: list[str], company: str = "") -> list[dict]:
        """Every hiring-relevant contact with a valid email, best tier first."""
        # ATS URLs give the board host, not the real company domain -> search by name
        params = {"limit": 10}
        if _is_ats_host(domain) and company:
            params["company"] = company
        else:
            params["domain"] = domain
        data, ok = self._get("domain-search", params)
        if not ok or not data:
            return []
        emails = data.get("data", {}).get("emails", [])
        out = []
        for best in _pick_all_by_title(emails, titles):
            if not _valid_syntax(best.get("value", "")):
                continue
            out.append({
                "company": company or domain,
                "full_name": " ".join(filter(None, [best.get("first_name"), best.get("last_name")])),
                "title": best.get("position"), "linkedin_url": best.get("linkedin"),
                "email": best["value"], "source_provider": "hunter",
                "confidence": (best.get("confidence") or 0) / 100.0,
                "verification_status": self._verify(best["value"]),
            })
        return out

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

    def find_all(self, domain: str, titles: list[str], company: str = "") -> list[dict]:
        return []


class _QuotaExhausted(Exception):
    pass


# Only these titles are worth emailing about a job. If none match -> skip the company
# entirely (never email a random Director of Investments / Quant analyst / etc.).
_HIRING_TITLES = (
    "founder", "co-founder", "cofounder", "ceo", "cto", "chief technology",
    "head of engineering", "vp engineering", "vp of engineering", "engineering manager",
    "engineering director", "director of engineering", "tech lead", "technical lead",
    "ai lead", "ml lead", "head of ai", "head of ml",
    "recruiter", "talent", "recruiting", "hr", "human resources", "people operations",
    "hiring", "talent acquisition",
)


def _pick_by_title(items: list[dict], titles: list[str]) -> Optional[dict]:
    """Return the best hiring-relevant contact, or None if there isn't one (skip company).
    Word-boundary match, not substring — 'cto' must never match inside 'direCTOr'."""
    all_ = _pick_all_by_title(items, titles)
    return all_[0] if all_ else None


def _pick_all_by_title(items: list[dict], titles: list[str]) -> list[dict]:
    """ALL hiring-relevant contacts, best tier first (founder/CTO before recruiter).
    Empty list if nobody is hiring-relevant — never email a random employee."""
    out = []
    for want in _HIRING_TITLES:
        pat = re.compile(rf"\b{re.escape(want)}\b")
        for it in items:
            if it not in out and pat.search((it.get("position") or "").lower()):
                out.append(it)
    return out


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


def discover_contacts(db: DB, company: str, domain: str,
                      titles: list[str] | None = None) -> list[dict]:
    """Run the waterfall; store and return ALL valid contacts, best tier first.
    The first provider that yields anything wins (no cross-provider mixing)."""
    if not domain:
        return []
    titles = titles or TARGET_TITLES
    for prov in _load_providers():
        found = prov.find_all(domain, titles, company)
        stored = []
        for contact in found:
            contact["company"] = company  # store under display name, not raw domain
            cid = db.save_contact(contact)
            if cid is None:
                continue
            contact["id"] = cid
            stored.append(contact)
            db.log("contacts", "company", company, "found",
                   {"provider": contact["source_provider"], "status": contact["verification_status"]})
        if stored:
            return stored
    db.log("contacts", "company", company, "not_found", {"domain": domain})
    return []


def discover_contact(db: DB, company: str, domain: str) -> Optional[dict]:
    """Back-compat single-contact wrapper: the best (highest-tier) contact, or None."""
    all_ = discover_contacts(db, company, domain)
    return all_[0] if all_ else None


def providers_configured() -> bool:
    return bool(_load_providers())
