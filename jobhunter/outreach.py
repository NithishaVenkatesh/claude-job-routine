"""Outreach, wired so the routine's CLAUDE agent does the two things that need
judgment or external access, and this code does everything deterministic around it.

Flow (cloud routine):
  1. prepare(db,p)            -> writes data/contact_tasks.json = shortlisted jobs that
                                  need a contact (company, domain, target titles, job info).
  2. [Claude, via MCP]        -> finds ALL valid contacts per company (decision-makers
                                  first; founder/CTO only for speculative leads), writes
                                  data/found_contacts.json.
  3. build_tasks(db,p)        -> stores those contacts, joins job + research + profile,
                                  writes data/outreach_tasks.json (one email task each).
  4. [Claude]                 -> writes each email -> data/outreach_drafts.json.
  5. commit(db,p)             -> validates + queues (or sends if autosend+Gmail).

If Python contact-provider keys (Apollo REST / Hunter) are ever configured, prepare()
resolves contacts itself and skips straight to step 3's output.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .config import ROOT
from .db import DB
from .models import JobPosting
from .profile import Profile

import os as _os

CONTACT_TASKS_PATH = ROOT / "data" / "contact_tasks.json"
FOUND_CONTACTS_PATH = ROOT / "data" / "found_contacts.json"
TASKS_PATH = ROOT / "data" / "outreach_tasks.json"
DRAFTS_PATH = ROOT / "data" / "outreach_drafts.json"
FIXED_TEMPLATE_PATH = ROOT / "config" / "outreach_template.md"
# Outreach breadth is quality-gated, NOT count-capped: every shortlisted job that clears
# OUTREACH_MIN_PROB proceeds. OUTREACH_MAX is only a budget guard against provider-quota
# blowups (candidates are processed best-first, so a quota spillover costs the weak tail).
# OUTREACH_TOP_N is honored as a legacy env override.
OUTREACH_MAX = int(_os.environ.get("OUTREACH_MAX", _os.environ.get("OUTREACH_TOP_N", "500")))
OUTREACH_MIN_PROB = float(_os.environ.get("OUTREACH_MIN_PROB", "0.0"))


_BRANDS = {
    "openai": "OpenAI", "phonepe": "PhonePe", "scaleai": "Scale AI", "huggingface": "Hugging Face",
    "elevenlabs": "ElevenLabs", "langchain": "LangChain", "clickhouse": "ClickHouse",
    "databricks": "Databricks", "anthropic": "Anthropic", "postman": "Postman", "vercel": "Vercel",
    "stripe": "Stripe", "ramp": "Ramp", "neon": "Neon", "mistral": "Mistral", "cred": "CRED",
    "razorpay": "Razorpay", "cursor": "Cursor", "replit": "Replit", "modal": "Modal", "micro1": "micro1",
}


def _pretty_company(name: str) -> str:
    key = name.lower().strip()
    if key in _BRANDS:
        return _BRANDS[key]
    if name.islower():                       # lowercase ATS token -> Title Case
        return " ".join(w.capitalize() for w in name.split())
    return name                              # already mixed-case (e.g. "Sarvam AI")


def _clean_company(name: str) -> str:
    """Strip trailing tags like '(YC W22)' / '(Series A)' then apply brand casing."""
    import re as _re
    return _pretty_company(_re.sub(r"\s*\([^)]*\)\s*$", "", name or "").strip())


def _first_name(full_name: str) -> str:
    return full_name.split()[0] if (full_name or "").strip() else "there"


def render_fixed_drafts() -> int:
    """If config/outreach_template.md exists, render every task with it (fill [Name] and
    [Company]) and write outreach_drafts.json deterministically — no LLM writing.
    Returns the number of drafts rendered; 0 if no fixed template or no tasks."""
    if not FIXED_TEMPLATE_PATH.exists() or not TASKS_PATH.exists():
        return 0
    raw = FIXED_TEMPLATE_PATH.read_text()
    # First line "Subject: ..." is the subject; the rest is the body.
    lines = raw.strip().splitlines()
    subject_tpl, body_tpl = "", raw.strip()
    if lines and lines[0].lower().startswith("subject:"):
        subject_tpl = lines[0][len("subject:"):].strip()
        body_tpl = "\n".join(lines[1:]).strip()

    tasks = json.loads(TASKS_PATH.read_text()).get("tasks", [])
    drafts = []
    for t in tasks:
        first_name = _first_name((t.get("contact") or {}).get("full_name") or "")
        company = _clean_company((t.get("job") or {}).get("company") or "")
        def fill(s: str) -> str:
            return s.replace("[Name]", first_name).replace("[Company]", company)
        drafts.append({"id": t["id"], "subject": fill(subject_tpl),
                       "body": fill(body_tpl), "hook_note": "fixed template"})
    DRAFTS_PATH.write_text(json.dumps({"drafts": drafts}, indent=2))
    return len(drafts)


@dataclass
class PrepareResult:
    considered: int = 0
    need_contact: int = 0
    resolved_by_code: int = 0          # when Python providers are configured
    mode: str = "mcp"                  # "mcp" (agent finds via Apollo MCP) | "code"
    skipped: dict = field(default_factory=dict)

    def _skip(self, r: str):
        self.skipped[r] = self.skipped.get(r, 0) + 1


@dataclass
class BuildResult:
    contacts_in: int = 0
    stored: int = 0
    tasks_written: int = 0
    skipped: dict = field(default_factory=dict)

    def _skip(self, r: str):
        self.skipped[r] = self.skipped.get(r, 0) + 1


@dataclass
class CommitResult:
    drafts_read: int = 0
    queued: int = 0
    sent: int = 0
    failed: int = 0
    held: dict = field(default_factory=dict)
    mode: str = "shadow"

    def _hold(self, r: str):
        self.held[r] = self.held.get(r, 0) + 1


# --------------------------------------------------------------------------
def _job_from_row(r) -> JobPosting:
    from datetime import datetime
    posted = None
    if r["posted_at"]:
        try:
            posted = datetime.fromisoformat(r["posted_at"])
        except ValueError:
            posted = None
    return JobPosting(source=r["source"], source_company=r["source_company"],
                      external_id=r["external_id"], title=r["title"], company=r["company"],
                      url=r["url"], location=r["location"] or "", remote=bool(r["remote"]),
                      employment_type=r["employment_type"] or "", description=r["description"] or "",
                      posted_at=posted)


def _job_from_fields(j: dict) -> JobPosting:
    return JobPosting(source="", source_company="", external_id="",
                      title=j.get("title", ""), company=j.get("company", ""),
                      url=j.get("url", ""), location=j.get("location", "") or "",
                      description=j.get("description", ""))


def _profile_block(p: Profile) -> dict:
    return {"name": p.name, "summary": p.summary_for_llm(),
            "resume_excerpt": (p.resume_text or "")[:3000]}


# --------------------------------------------------------------------------
def _bucket_of(posted_at: str | None) -> str:
    from datetime import datetime, timezone
    if not posted_at:
        return "unknown"
    try:
        h = (datetime.now(timezone.utc) - datetime.fromisoformat(posted_at)).total_seconds() / 3600
    except ValueError:
        return "unknown"
    return "24h" if h <= 24 else "48h" if h <= 48 else "72h" if h <= 72 else "7d" if h <= 168 else "older"


_BUCKET_ORDER = {"24h": 0, "48h": 1, "72h": 2, "7d": 3, "unknown": 4}


def _freshest_first(rows, limit: int):
    """AGENT_SPEC Stage 4: fill outreach slots from <24h jobs FIRST; expand to 48h,
    72h, 7d, then UNDATED leads as the final fill tier (never silently dropped —
    speculative/founder-post leads usually carry no posting date). Never include >7d."""
    buckets = {"24h": [], "48h": [], "72h": [], "7d": [], "unknown": []}
    for r in rows:
        b = _bucket_of(r["posted_at"])
        if b in buckets:
            buckets[b].append(r)
    picked = []
    for b in ("24h", "48h", "72h", "7d", "unknown"):
        if len(picked) >= limit:
            break
        picked.extend(buckets[b][: limit - len(picked)])
    return picked


def _lead_type(description: str | None) -> str:
    d = (description or "").lower()
    return "speculative" if ("[speculative]" in d or "[funding-trail]" in d) else "posted"


def prepare(db: DB, p: Profile, limit: int | None = None) -> PrepareResult:
    from . import contacts as contacts_mod, research as research_mod
    res = PrepareResult()

    # Quality-gated, count-uncapped: everything shortlisted above the probability floor
    # proceeds. OUTREACH_MAX is a provider-quota budget guard, not a quality filter.
    ceiling = limit or OUTREACH_MAX
    pool = [r for r in db.shortlist(limit=ceiling, min_prob=OUTREACH_MIN_PROB)
            if _bucket_of(r["posted_at"]) in _BUCKET_ORDER]      # never >7d-old
    # Best-first: interview probability decides, freshness breaks ties — if a
    # quota/wall-clock limit cuts the run short, only the weakest tail spills over.
    pool.sort(key=lambda r: (-(r["interview_prob"] or 0.0),
                             _BUCKET_ORDER[_bucket_of(r["posted_at"])]))
    # Checkpoint/resume: a job that already has a drafted/queued/sent email (this run
    # or restored from prior-run state) is done — don't burn contact quota on it again.
    candidates = []
    for r in pool:
        if db.job_has_email(r["content_hash"]):
            res._skip("already drafted (prior run)")
            continue
        candidates.append(r)
    res.considered = len(candidates)

    if contacts_mod.providers_configured():
        # Python-provider path: resolve now and write outreach_tasks.json directly.
        res.mode = "code"
        _prepare_via_code(db, p, candidates, res)
        return res

    # MCP path: emit the "needs a contact" list for the agent to resolve via MCP.
    from .contacts import TARGET_TITLES, SPECULATIVE_TITLES
    tasks = []
    for r in candidates:
        job = _job_from_row(r)
        domain = research_mod.domain_from_url(job.url)
        lead = _lead_type(job.description)
        tasks.append({
            "job_hash": job.content_hash(),
            "company": job.company,
            "domain": domain,
            "lead_type": lead,
            "target_titles": SPECULATIVE_TITLES if lead == "speculative" else TARGET_TITLES,
            "job": {"title": job.title, "url": job.url, "location": job.location,
                    "description": (job.description or "")[:1200]},
        })
    res.need_contact = len(tasks)
    CONTACT_TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTACT_TASKS_PATH.write_text(json.dumps({
        "instructions": (
            "For EACH task, use the contact-finding MCP tools to find EVERY person at the "
            "company who matches the task's target_titles and has a REAL, verified/"
            "high-confidence professional email — return ALL of them, not just one "
            "(multiple contacts per job_hash are expected and handled downstream). Search "
            "by the COMPANY NAME (the 'domain' field is often the ATS host like "
            "greenhouse.io, not the real company domain — ignore it if so). target_titles "
            "is a PRIORITY order, decision-makers first: resolve founder/CTO/eng-lead "
            "contacts before recruiters, so if quota runs out you got the ones that "
            "convert. Tasks with lead_type='speculative' are freshly-funded startups with "
            "no public posting — contact ONLY the founder or CTO for those; that outreach "
            "is the whole play. Never guess or fabricate an email; never include pattern-"
            "constructed (first.last@) or 'not_unlocked' addresses. Write results to "
            "data/found_contacts.json as "
            "{\"contacts\":[{\"job_hash\":..., \"company\":..., \"full_name\":..., \"title\":..., "
            "\"email\":..., \"linkedin_url\":..., \"verification_status\":\"verified\", "
            "\"source\":\"vibe\"}]}. Skip tasks where no verified email is found."),
        "tasks": tasks,
    }, indent=2))
    db.log("outreach", "prepare", "-", "contact_tasks", {"need_contact": len(tasks)})
    return res


def _prepare_via_code(db, p, candidates, res: PrepareResult):
    from . import contacts as contacts_mod, research as research_mod
    from .email_draft import choose_template, _load_style_guide, _load_template
    from .contacts import TARGET_TITLES, SPECULATIVE_TITLES
    tasks = []
    for r in candidates:
        job = _job_from_row(r)
        research = research_mod.research_company(db, job)
        domain = research_mod.domain_from_url(job.url)
        titles = SPECULATIVE_TITLES if _lead_type(job.description) == "speculative" \
            else TARGET_TITLES
        found = contacts_mod.discover_contacts(db, job.company, domain, titles)
        if not found:
            res._skip("no verified contact found")
            continue
        for contact in found:      # one email task per valid contact, best tier first
            if contact.get("id") and db.email_exists(contact["id"], job.content_hash()):
                res._skip("already emailed")
                continue
            res.resolved_by_code += 1
            tasks.append(_email_task(job, contact, research, choose_template, _load_template))
    _write_outreach_tasks(p, tasks)
    render_fixed_drafts()


# --------------------------------------------------------------------------
def build_tasks(db: DB, p: Profile) -> BuildResult:
    """Consume the agent's found_contacts.json (from Apollo MCP) -> outreach_tasks.json."""
    from . import research as research_mod
    from .email_draft import choose_template, _load_template
    res = BuildResult()

    if not FOUND_CONTACTS_PATH.exists() or not CONTACT_TASKS_PATH.exists():
        _write_outreach_tasks(p, [])
        res._skip("missing found_contacts.json or contact_tasks.json")
        return res

    ctx = {t["job_hash"]: t for t in json.loads(CONTACT_TASKS_PATH.read_text()).get("tasks", [])}
    found = json.loads(FOUND_CONTACTS_PATH.read_text()).get("contacts", [])
    # Multiple contacts per job_hash are expected (all valid people at a company).
    # Process decision-makers first so drafts land in the report best-first.
    from .contacts import tier_rank
    found.sort(key=lambda c: tier_rank(c.get("title")))
    tasks = []
    for c in found:
        res.contacts_in += 1
        jh = c.get("job_hash")
        task_ctx = ctx.get(jh)
        if not task_ctx:
            res._skip("contact job_hash not in contact_tasks")
            continue
        if (c.get("verification_status") or "").lower() != "verified" or not c.get("email"):
            res._skip("unverified or missing email")
            continue
        cid = db.save_contact({
            "company": c.get("company") or task_ctx["company"],
            "full_name": c.get("full_name"), "title": c.get("title"),
            "linkedin_url": c.get("linkedin_url"), "email": c.get("email"),
            "source_provider": c.get("source", "apollo_mcp"), "confidence": 0.9,
            "verification_status": "verified",
        })
        if cid is None:
            res._skip("contact save failed / dup")
            continue
        if db.email_exists(cid, jh):
            res._skip("already emailed")
            continue
        res.stored += 1
        job = _job_from_fields({**task_ctx["job"], "company": task_ctx["company"]})
        research = research_mod.research_company(db, job)
        contact = {"id": cid, "full_name": c.get("full_name"), "title": c.get("title"),
                   "email": c.get("email"), "verification_status": "verified",
                   "linkedin_url": c.get("linkedin_url")}
        tasks.append(_email_task(job, contact, research, choose_template, _load_template, jh))
    _write_outreach_tasks(p, tasks)
    res.tasks_written = len(tasks)
    rendered = render_fixed_drafts()   # fixed template -> drafts, deterministically
    db.log("outreach", "build_tasks", "-", "done",
           {"stored": res.stored, "tasks": res.tasks_written, "rendered": rendered})
    return res


def _email_task(job, contact, research, choose_template, load_template, job_hash=None):
    jh = job_hash or job.content_hash()
    tclass = choose_template(contact.get("title", ""))
    return {
        "id": f"{contact['id']}:{jh}", "contact_id": contact["id"], "job_hash": jh,
        "job": {"title": job.title, "company": job.company, "url": job.url,
                "location": job.location, "description": (job.description or "")[:1500]},
        "contact": {"full_name": contact.get("full_name"), "title": contact.get("title"),
                    "email": contact.get("email"),
                    "verification_status": contact.get("verification_status"),
                    "confidence": contact.get("confidence"),
                    "linkedin_url": contact.get("linkedin_url")},
        "research": research, "template_class": tclass, "template_intent": load_template(tclass),
    }


def _write_outreach_tasks(p: Profile, tasks: list):
    from .email_draft import _load_style_guide
    TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TASKS_PATH.write_text(json.dumps({
        "profile": _profile_block(p),
        "style_guide": _load_style_guide(),
        "instructions": (
            "You (Claude) write each email yourself. 120-160 words. One TRUE specific hook from "
            "the job/research. Reference one real project + real outcome from the profile. Follow "
            "the style guide. Never invent facts, skills, or company details. Ban cliches: "
            "'I hope this finds you well', 'I am reaching out', 'I came across'."),
        "tasks": tasks,
    }, indent=2))


# --------------------------------------------------------------------------
def commit(db: DB, p: Profile) -> CommitResult:
    import time
    from . import validate as validate_mod, send as send_mod, pacing
    res = CommitResult()
    if not DRAFTS_PATH.exists():
        res._hold("no drafts file (agent wrote nothing)")
        return res
    if not TASKS_PATH.exists():
        res._hold("no tasks file")
        return res

    tasks = {t["id"]: t for t in json.loads(TASKS_PATH.read_text()).get("tasks", [])}
    drafts = json.loads(DRAFTS_PATH.read_text()).get("drafts", [])
    rules = validate_mod.load_rules()
    autosend = bool(rules.get("autosend_enabled", False))
    res.mode = "live" if (autosend and send_mod.sender_ready()) else "shadow"
    if res.mode == "live" and pacing.within_quiet_hours(rules):
        res.mode = "quiet"   # queue everything; next daytime run sends
    breaker = pacing.CircuitBreaker(int(rules.get("max_consecutive_send_failures", 3)))

    for d in drafts:
        res.drafts_read += 1
        task = tasks.get(d.get("id"))
        if not task:
            res._hold("draft id not found in tasks")
            continue
        job = _job_from_fields(task["job"])
        contact = dict(task["contact"]); contact["id"] = task["contact_id"]
        draft = {"subject": d.get("subject"), "body": d.get("body"),
                 "hook_note": d.get("hook_note"), "template_class": task.get("template_class")}

        verdict = validate_mod.validate(db, rules, job, contact, draft)
        row = {"job_hash": task["job_hash"], "contact_id": task["contact_id"],
               "company": job.company, "to_email": contact.get("email"),
               "template_class": draft["template_class"], "subject": draft["subject"],
               "body": draft["body"], "hook_note": draft["hook_note"],
               "safety": {"validate": verdict.reasons}, "status": "draft"}

        if not (res.mode == "live" and verdict.ok) or breaker.open:
            row["status"] = "queued"
            db.save_email(row)
            res.queued += 1
            if breaker.open:
                res._hold("send circuit breaker open (consecutive failures)")
            elif verdict.reasons:
                res._hold(verdict.reasons[0])
            else:
                res._hold("quiet hours (queued for daytime)" if res.mode == "quiet" else "shadow mode")
            continue

        # human-like pacing: never machine-gun a batch (AGENT_SPEC: 90-300s + jitter)
        if res.sent + res.failed > 0:
            delay = pacing.send_delay_seconds(rules)
            db.log("send", "pacing", "-", "sleep", {"seconds": round(delay, 1)})
            time.sleep(delay)

        ok, info = send_mod.send_email(contact["email"], draft["subject"], draft["body"], from_name=p.name)
        breaker.record(ok)
        if ok:
            row["status"] = "sent"
            eid = db.save_email(row)
            if eid:
                from datetime import datetime, timezone
                db.update_email(eid, status="sent", message_id=info,
                                sent_at=datetime.now(timezone.utc).isoformat())
            res.sent += 1
        else:
            row["status"] = "failed"
            eid = db.save_email(row)
            if eid:
                db.update_email(eid, error=info)
            res.failed += 1
            res._hold("send failed")
    return res
