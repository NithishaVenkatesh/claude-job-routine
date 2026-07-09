"""Stage 16-17: personalized email drafting + Stage 18 content-safety pass.

Truth constraints (AGENT_SPEC): never fabricate achievements/skills/company facts.
The draft is grounded in the resume, the JD, and the GROUNDED company research only.
A second LLM pass audits the draft for hallucinations / wrong names / AI-smell before
it is allowed to be sent; failures get queued for human review instead of sent.
"""
from __future__ import annotations

from pathlib import Path

from .ai import client
from .config import ROOT, load_yaml
from .models import JobPosting
from .profile import Profile

_DRAFT_SYSTEM = """You write a short, genuine cold outreach email from a job seeker to a hiring
contact. Rules:
- Use ONLY facts present in the candidate profile/resume and the job/company info given. Never
  invent achievements, skills, metrics, or company facts.
- One specific, true hook about the company/role. No generic flattery.
- Sound like a real motivated engineer, not AI. Ban these openers: "I hope this email finds you
  well", "I am reaching out", "I came across". Vary sentence structure. 120-160 words max.
- End with a low-friction ask (a quick chat / whether they're the right person).
Return ONLY JSON: {"subject": "...", "body": "...", "hook_note": "<the true detail you used>"}"""

_SAFETY_SYSTEM = """You are a strict pre-send auditor for a job-outreach email. Check for:
- any claim NOT supported by the candidate profile or job/company facts (hallucination),
- wrong recipient name or wrong company,
- obvious AI-generated tone / banned clichés,
- broken/placeholder text (e.g. [Company], TODO, XXXX).
Return ONLY JSON: {"safe": <true|false>, "issues": ["..."], "reason": "<short>"}"""


def _load_style_guide() -> str:
    p = ROOT / "config" / "style_guide.md"
    return p.read_text() if p.exists() else ""


def _load_template(template_class: str) -> str:
    p = ROOT / "config" / "templates" / f"{template_class}.md"
    if p.exists():
        return p.read_text()
    generic = ROOT / "config" / "templates" / "generic.md"
    return generic.read_text() if generic.exists() else ""


def choose_template(title: str) -> str:
    t = (title or "").lower()
    if any(k in t for k in ("founder", "cto", "co-founder")):
        return "founder"
    if any(k in t for k in ("recruiter", "talent", "hr")):
        return "recruiter"
    return "hiring_manager"


def draft_email(profile: Profile, job: JobPosting, contact: dict, research: dict) -> dict | None:
    c = client()
    if not c.available():
        return None
    template_class = choose_template(contact.get("title", ""))
    user = f"""CANDIDATE PROFILE:
{profile.summary_for_llm()}

RESUME (source of truth for facts):
{profile.resume_text[:3000]}

CONTACT: {contact.get('full_name')} — {contact.get('title')} at {job.company}
JOB: {job.title} ({job.url})
JOB DESCRIPTION (truncated):
{(job.description or '')[:1500]}

GROUNDED COMPANY RESEARCH (only true facts):
{research}

STYLE GUIDE:
{_load_style_guide()}

TEMPLATE (intent skeleton, do not fill blindly):
{_load_template(template_class)}
"""
    data = c.complete_json(user, system=_DRAFT_SYSTEM, max_tokens=700)
    if not data or not data.get("subject") or not data.get("body"):
        return None
    data["template_class"] = template_class
    return data


def safety_check(profile: Profile, job: JobPosting, contact: dict, draft: dict) -> dict:
    """Second-pass audit. On any failure (or no LLM) returns safe=False so the email
    is queued for review rather than auto-sent."""
    c = client()
    if not c.available():
        return {"safe": False, "issues": ["no LLM to audit"], "reason": "audit unavailable"}
    user = f"""RECIPIENT: {contact.get('full_name')} at {job.company}
CANDIDATE FACTS (resume): {profile.resume_text[:2000]}
COMPANY FACTS: {job.company}; {research_hint(job)}
EMAIL SUBJECT: {draft.get('subject')}
EMAIL BODY:
{draft.get('body')}"""
    data = c.complete_json(user, system=_SAFETY_SYSTEM, max_tokens=400)
    if not data:
        return {"safe": False, "issues": ["audit parse failed"], "reason": "audit unavailable"}
    return data


def research_hint(job: JobPosting) -> str:
    return (job.description or "")[:400]
