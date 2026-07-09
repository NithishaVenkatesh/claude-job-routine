"""Hacker News "Ask HN: Who is hiring?" — startups post here BEFORE job boards.
Public Algolia API, no auth. Each top-level comment in the monthly thread is one
job post (convention: "Company | Role | Location | ...").
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from ..models import JobPosting
from .base import Source, get_json, looks_remote


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", html.unescape(s or "")).strip()


class HackerNews(Source):
    type_name = "hackernews"

    def fetch(self) -> list[JobPosting]:
        # find the most recent "Who is hiring" thread (posted monthly by whoishiring)
        data = get_json("https://hn.algolia.com/api/v1/search_by_date",
                        params={"query": "Ask HN: Who is hiring?",
                                "tags": "story,author_whoishiring", "hitsPerPage": 1})
        hits = data.get("hits", [])
        if not hits:
            return []
        story_id = hits[0]["objectID"]

        # pull the thread's comments (each top-level comment = one job post)
        thread = get_json(f"https://hn.algolia.com/api/v1/items/{story_id}")
        out: list[JobPosting] = []
        for c in (thread.get("children") or []):
            text = _strip_html(c.get("text") or "")
            if len(text) < 40:  # noise / deleted
                continue
            first_line = text.split("\n")[0][:200]
            # convention: Company | Role | Location | salary/other
            parts = [p.strip() for p in first_line.split("|")]
            company = parts[0][:80] if parts else "HN poster"
            title = parts[1][:120] if len(parts) > 1 else first_line[:120]
            location = parts[2][:80] if len(parts) > 2 else ""
            created = c.get("created_at")
            posted = None
            if created:
                try:
                    posted = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except ValueError:
                    posted = None
            out.append(JobPosting(
                source=self.type_name, source_company="whoishiring",
                external_id=str(c.get("id", "")),
                title=title, company=company,
                url=f"https://news.ycombinator.com/item?id={c.get('id')}",
                location=location, remote=looks_remote(text[:600]),
                description=text[:4000], posted_at=posted, raw={},
            ))
        return out
