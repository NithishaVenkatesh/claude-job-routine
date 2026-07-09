"""Stage 13: company research — GROUNDED only.

Truth constraint (AGENT_SPEC): never fabricate company facts. Without a web-search API
key, the only grounded source is the job description itself, so research extracts real
signals (tech stack, mission language, what the team builds) from the JD rather than
inventing funding/news. Richer research (funding, recent news) needs a search API
(SerpAPI/JSearch) — wired as an optional upgrade, off by default.

Results are cached per company for 30 days.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .ai import client
from .db import DB
from .models import JobPosting

_SYSTEM = ("You extract outreach-useful, FACTUAL signals from a job description. Only use what is "
           "explicitly present in the text. Never invent funding, news, headcount, or customers. "
           "If a field isn't in the text, return an empty string for it.")

_PROMPT = """Company: {company}
Job title: {title}
Job description:
{description}

Extract ONLY what the description actually states. Return JSON:
{{"what_they_build": "<1 sentence, grounded>",
  "tech_stack": ["<tech explicitly mentioned>", ...],
  "mission_language": "<a real phrase about mission/values if present, else ''>",
  "team_context": "<what this team/role does, grounded>",
  "outreach_hook": "<one specific, true detail worth mentioning in an email, or ''>"}}"""


def domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def research_company(db: DB, job: JobPosting, max_age_days: int = 30) -> dict:
    cached = db.get_company_research(job.company)
    if cached:
        return cached["research"]

    domain = domain_from_url(job.url)
    c = client()
    if c.available():
        data = c.complete_json(
            _PROMPT.format(company=job.company, title=job.title,
                           description=(job.description or "")[:4000]),
            system=_SYSTEM, max_tokens=500)
    else:
        data = None

    if not data:
        # deterministic, still-grounded fallback: pull tech tokens straight from the JD text
        data = {
            "what_they_build": "",
            "tech_stack": _tech_from_text(job.description),
            "mission_language": "",
            "team_context": job.title,
            "outreach_hook": "",
            "_note": "LLM unavailable; tech stack extracted verbatim from JD",
        }
    db.save_company_research(job.company, domain, data)
    return data


_TECH = ["fastapi", "django", "flask", "python", "typescript", "react", "next.js", "node",
         "go", "rust", "kubernetes", "docker", "aws", "gcp", "azure", "postgres", "postgresql",
         "kafka", "spark", "pytorch", "tensorflow", "langchain", "langgraph", "rag", "llm",
         "pinecone", "milvus", "weaviate", "openai", "anthropic", "graphql", "grpc"]


def _tech_from_text(text: str) -> list[str]:
    t = (text or "").lower()
    return [tech for tech in _TECH if re.search(rf"\b{re.escape(tech)}\b", t)]
