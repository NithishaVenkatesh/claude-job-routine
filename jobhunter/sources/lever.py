"""Lever postings API (public, no auth).
Endpoint: https://api.lever.co/v0/postings/{company}?mode=json
"""
from __future__ import annotations

from ..models import JobPosting
from .base import Source, get_json, parse_epoch_ms, looks_remote


class Lever(Source):
    type_name = "lever"

    def fetch(self) -> list[JobPosting]:
        url = f"https://api.lever.co/v0/postings/{self.company}"
        data = get_json(url, params={"mode": "json"})
        out: list[JobPosting] = []
        for j in data if isinstance(data, list) else []:
            cats = j.get("categories") or {}
            location = cats.get("location", "") or ""
            commitment = cats.get("commitment", "") or ""
            title = j.get("text", "") or ""
            workplace = j.get("workplaceType", "") or ""
            out.append(JobPosting(
                source=self.type_name,
                source_company=self.company,
                external_id=str(j.get("id", "")),
                title=title,
                company=self.company,
                url=j.get("hostedUrl", "") or "",
                location=location,
                remote=looks_remote(location, workplace, title),
                employment_type=commitment,
                description=j.get("descriptionPlain", "") or "",
                posted_at=parse_epoch_ms(j.get("createdAt")),
                raw=j,
            ))
        return out
