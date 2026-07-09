"""Stage 8-9: score jobs against the profile.

Two stages (BLUEPRINT §4 Phase 3):
  1. heuristic_score()  — free, fast, deterministic. Runs on EVERY job. Also applies
     hard reject rules. Produces a 0-100 match score + a rough interview-probability proxy.
  2. llm_score()        — optional (needs ANTHROPIC_API_KEY). Re-scores only the top-N
     heuristic survivors with a calibrated interview probability + rationale.

The north-star (AGENT_SPEC): rank by INTERVIEW PROBABILITY, not volume. The heuristic is
intentionally conservative — it rejects aggressively so outreach effort concentrates on
genuine fits.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .models import JobPosting
from .profile import Profile

_YEARS_RE = re.compile(r"(\d+)\s*\+?\s*(?:-\s*\d+\s*)?years?", re.I)

# Distinctive tokens that signal a genuinely AI/backend role (vs generic "engineer").
_DOMAIN_TOKENS = {
    "ai", "ml", "llm", "genai", "rag", "backend", "python", "machine",
    "learning", "generative", "agentic", "agent", "agents", "nlp", "data",
}
# Stretch-seniority markers: not auto-rejected, but lower interview odds for a junior.
_STRETCH_SENIORITY = re.compile(r"\b(senior|sr\.?|lead)\b", re.I)


@dataclass
class Score:
    content_hash: str
    score: float                 # 0-100 resume/role match
    interview_prob: float        # 0-1 (heuristic proxy unless llm-refined)
    dimensions: dict = field(default_factory=dict)
    rationale: str = ""
    reject_reason: Optional[str] = None
    model: str = "heuristic"

    @property
    def rejected(self) -> bool:
        return self.reject_reason is not None


def _title_match(title: str, roles: list[str]) -> float:
    """1.0 if a target role phrase is in the title, else best token-overlap fraction."""
    t = title.lower()
    for r in roles:
        if r and r in t:
            return 1.0
    best = 0.0
    t_tokens = set(re.findall(r"[a-z]+", t))
    for r in roles:
        r_tokens = set(re.findall(r"[a-z]+", r))
        if not r_tokens:
            continue
        overlap = len(t_tokens & r_tokens) / len(r_tokens)
        best = max(best, overlap)
    return best


def _skill_overlap(text: str, core: list[str], secondary: list[str]) -> tuple[float, list[str]]:
    t = text.lower()
    hits, weight = [], 0.0
    for s in core:
        if s and s in t:
            hits.append(s)
            weight += 2.0
    for s in secondary:
        if s and s in t:
            hits.append(s)
            weight += 1.0
    denom = 2.0 * max(1, len(core)) + 1.0 * max(1, len(secondary))
    # normalize against a realistic ceiling (~a third of all skills present is a strong match)
    frac = min(1.0, weight / (denom * 0.33))
    return frac, hits


def _max_years_required(text: str) -> Optional[int]:
    vals = [int(m.group(1)) for m in _YEARS_RE.finditer(text or "")]
    # ignore absurd matches (e.g. "10000 years"); keep plausible experience figures
    vals = [v for v in vals if 0 < v <= 20]
    return max(vals) if vals else None


def heuristic_score(job: JobPosting, p: Profile) -> Score:
    h = job.content_hash()
    title = job.title.lower()
    blob = f"{job.title}\n{job.description}"

    # ---- hard rejects ----------------------------------------------------
    for kw in p.reject_role_keywords:
        if kw and kw in title:
            return Score(h, 0, 0, reject_reason=f"non-target role (title contains '{kw}')")

    for kw in p.too_senior_keywords:
        if kw and re.search(rf"\b{re.escape(kw)}\b", title):
            return Score(h, 0, 0, reject_reason=f"over-seniority (title contains '{kw}')")

    yrs = _max_years_required(job.description)
    if yrs is not None and yrs > p.max_years_required:
        return Score(h, 0, 0, dimensions={"years_required": yrs},
                     reject_reason=f"requires {yrs}+ yrs (> {p.max_years_required})")

    # ---- positive signals -------------------------------------------------
    title_fit = _title_match(job.title, p.target_roles)
    skill_fit, hits = _skill_overlap(blob, p.skills_core, p.skills_secondary)
    title_tokens = set(re.findall(r"[a-z]+", title))
    has_domain = bool(title_tokens & _DOMAIN_TOKENS)

    loc = (job.location or "").lower()
    remote_fit = 1.0 if (job.remote or any(l in loc for l in p.locations)) else 0.3

    # weighted composite (title and skills dominate; location is a modifier)
    score = 100 * (0.45 * title_fit + 0.40 * skill_fit + 0.15 * remote_fit)

    # a title that doesn't match any target role at all is almost certainly noise
    if title_fit < 0.34:
        return Score(h, round(score, 1), 0,
                     dimensions={"title_fit": round(title_fit, 2), "skill_fit": round(skill_fit, 2)},
                     reject_reason="title not aligned with target roles")

    # partial title matches (e.g. "...Solutions Engineer") only count if the title
    # also carries an AI/backend domain signal — kills generic false positives.
    if title_fit < 1.0 and not has_domain:
        return Score(h, round(score, 1), 0,
                     dimensions={"title_fit": round(title_fit, 2)},
                     reject_reason="generic title, no AI/backend domain signal")

    # heuristic interview-probability proxy: fit, discounted by recency-competition.
    # (fresher posts = smaller applicant pool = higher shortlist odds — AGENT_SPEC Stage 11)
    bucket = job.recency_bucket()
    recency_boost = {"24h": 1.0, "48h": 0.9, "72h": 0.8, "7d": 0.6, "older": 0.35, "unknown": 0.5}[bucket]
    # seniority stretch: a ~1yr candidate is a long shot for senior/lead titles
    stretch = 0.6 if _STRETCH_SENIORITY.search(title) else 1.0
    interview_prob = min(0.95, (score / 100.0) * recency_boost * stretch)

    dims = {
        "title_fit": round(title_fit, 2),
        "skill_fit": round(skill_fit, 2),
        "remote_fit": round(remote_fit, 2),
        "recency": bucket,
        "seniority_stretch": stretch < 1.0,
        "skills_hit": hits[:12],
    }
    rationale = (f"title~{title_fit:.0%}, skills {len(hits)} matched, "
                 f"{'remote/location ok' if remote_fit >= 1 else 'location weak'}, posted {bucket}"
                 f"{', senior-stretch' if stretch < 1.0 else ''}")
    return Score(h, round(score, 1), round(interview_prob, 2), dimensions=dims, rationale=rationale)
