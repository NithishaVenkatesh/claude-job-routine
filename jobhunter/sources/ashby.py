"""Ashby posting API (public, no auth).
Endpoint: https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true
Field names vary slightly across boards, so map defensively.
"""
from __future__ import annotations

import html
import re

from ..models import JobPosting
from .base import Source, get_json, parse_iso, looks_remote


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", html.unescape(s or "")).strip()


class Ashby(Source):
    type_name = "ashby"

    def fetch(self) -> list[JobPosting]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{self.company}"
        data = get_json(url, params={"includeCompensation": "true"})
        out: list[JobPosting] = []
        for j in data.get("jobs", []):
            location = j.get("location") or j.get("locationName") or ""
            title = j.get("title", "") or ""
            is_remote = bool(j.get("isRemote")) or looks_remote(location, title)
            desc = j.get("descriptionPlain") or _strip_html(j.get("descriptionHtml", ""))
            out.append(JobPosting(
                source=self.type_name,
                source_company=self.company,
                external_id=str(j.get("id", "")),
                title=title,
                company=j.get("organizationName") or self.company,
                url=j.get("jobUrl", "") or j.get("applyUrl", "") or "",
                location=location,
                remote=is_remote,
                employment_type=j.get("employmentType", "") or "",
                description=desc,
                posted_at=parse_iso(j.get("publishedAt") or j.get("publishedDate")),
                raw=j,
            ))
        return out
