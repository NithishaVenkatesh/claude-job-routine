"""We Work Remotely — public RSS feeds per category. Stdlib XML parsing, no deps."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import httpx

from ..models import JobPosting
from .base import Source, SourceError, USER_AGENT

FEEDS = {
    "programming": "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "devops": "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "fullstack": "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
}


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", html.unescape(s or "")).strip()


class WeWorkRemotely(Source):
    type_name = "weworkremotely"

    def fetch(self) -> list[JobPosting]:
        url = FEEDS.get(self.company, FEEDS["programming"])
        try:
            resp = httpx.get(url, timeout=20, headers={"User-Agent": USER_AGENT},
                             follow_redirects=True)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
        except Exception as e:
            raise SourceError(f"wwr fetch/parse failed: {e}")

        out: list[JobPosting] = []
        for item in root.iter("item"):
            title_raw = (item.findtext("title") or "").strip()
            # convention: "Company: Job Title"
            company, _, title = title_raw.partition(":")
            if not title:
                company, title = "unknown", title_raw
            posted = None
            pub = item.findtext("pubDate")
            if pub:
                try:
                    posted = parsedate_to_datetime(pub)
                except (ValueError, TypeError):
                    posted = None
            link = (item.findtext("link") or "").strip()
            out.append(JobPosting(
                source=self.type_name, source_company=self.company,
                external_id=link.rsplit("/", 1)[-1] or title_raw,
                title=title.strip()[:150], company=company.strip()[:80],
                url=link, location="Remote", remote=True,
                description=_strip_html(item.findtext("description") or "")[:4000],
                posted_at=posted, raw={},
            ))
        return out
