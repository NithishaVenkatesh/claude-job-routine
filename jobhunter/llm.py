"""LLM refinement of the top heuristic job matches (uses the unified AIClient).

No-ops cleanly when no provider is configured, so the system runs on the heuristic
alone and sharpens the moment an Azure OpenAI (or Anthropic) key is added.
"""
from __future__ import annotations

from typing import Optional

from .ai import client
from .models import JobPosting
from .profile import Profile
from .score import Score

_SYSTEM = ("You are an elite technical recruiter. You score one job for one candidate on a single "
           "metric: their probability of getting an interview if they apply — role fit, skill match, "
           "seniority fit, and likely competition. Never reward brand names. Favor roles where THIS "
           "candidate stands out. Be honest and calibrated.")

_PROMPT = """CANDIDATE PROFILE:
{profile}

JOB:
Title: {title}
Company: {company}
Location: {location} (remote={remote})
Description (truncated):
{description}

Return ONLY a JSON object:
{{"interview_prob": <0.0-1.0>, "score": <0-100 fit>, "reject": <true|false>,
  "reject_reason": <string or null>, "rationale": <one concrete sentence>}}"""


def available() -> bool:
    return client().available()


def llm_score(job: JobPosting, p: Profile) -> Optional[Score]:
    c = client()
    if not c.available():
        return None
    data = c.complete_json(
        _PROMPT.format(profile=p.summary_for_llm(), title=job.title, company=job.company,
                       location=job.location or "?", remote=job.remote,
                       description=(job.description or "")[:3500]),
        system=_SYSTEM, max_tokens=400)
    if not data:
        return None
    reject = bool(data.get("reject"))
    try:
        return Score(
            content_hash=job.content_hash(),
            score=float(data.get("score", 0)),
            interview_prob=float(data.get("interview_prob", 0)),
            rationale=str(data.get("rationale", ""))[:400],
            reject_reason=(str(data.get("reject_reason")) if reject else None),
            model=f"llm:{c.provider}",
        )
    except (TypeError, ValueError):
        return None
