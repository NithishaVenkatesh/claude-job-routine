"""RemoteOK — public JSON API of remote jobs (remoteok.com/api). Free; they ask for
a link-back when displaying jobs publicly, which a personal job search satisfies.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..models import JobPosting
from .base import Source, get_json


class RemoteOK(Source):
    type_name = "remoteok"

    def fetch(self) -> list[JobPosting]:
        data = get_json("https://remoteok.com/api")
        out: list[JobPosting] = []
        for j in data if isinstance(data, list) else []:
            if not isinstance(j, dict) or not j.get("position"):
                continue  # first element is a legal notice
            posted = None
            if j.get("epoch"):
                try:
                    posted = datetime.fromtimestamp(int(j["epoch"]), tz=timezone.utc)
                except (ValueError, TypeError):
                    posted = None
            tags = " ".join(j.get("tags") or [])
            out.append(JobPosting(
                source=self.type_name, source_company="remoteok",
                external_id=str(j.get("id", "")),
                title=j.get("position", ""), company=j.get("company", "") or "unknown",
                url=j.get("url", "") or "", location=j.get("location", "") or "Remote",
                remote=True,
                description=((j.get("description") or "")[:4000] + f"\ntags: {tags}"),
                posted_at=posted, raw={},
            ))
        return out
