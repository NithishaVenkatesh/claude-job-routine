"""Greenhouse job board API (public, no auth).
Endpoint: https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
"""
from __future__ import annotations

import html
import re

from ..models import JobPosting
from .base import Source, get_json, parse_iso, looks_remote


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", html.unescape(s or "")).strip()


class Greenhouse(Source):
    type_name = "greenhouse"

    def fetch(self) -> list[JobPosting]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.company}/jobs"
        data = get_json(url, params={"content": "true"})
        out: list[JobPosting] = []
        for j in data.get("jobs", []):
            location = (j.get("location") or {}).get("name", "") or ""
            title = j.get("title", "") or ""
            out.append(JobPosting(
                source=self.type_name,
                source_company=self.company,
                external_id=str(j.get("id", "")),
                title=title,
                company=self.company,
                url=j.get("absolute_url", "") or "",
                location=location,
                remote=looks_remote(location, title),
                description=_strip_html(j.get("content", "")),
                posted_at=parse_iso(j.get("updated_at")),
                raw=j,
            ))
        return out
