"""Normalized data models shared across sources and stages."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


def _norm_text(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


@dataclass
class JobPosting:
    """A single job, normalized to one shape regardless of source ATS."""

    source: str                 # "greenhouse" | "lever" | "ashby" | ...
    source_company: str         # the board token/slug we queried
    external_id: str            # id within that source
    title: str
    company: str
    url: str
    location: str = ""
    remote: bool = False
    employment_type: str = ""
    description: str = ""
    posted_at: Optional[datetime] = None   # tz-aware UTC when known
    raw: dict = field(default_factory=dict, repr=False)

    def content_hash(self) -> str:
        """Stable dedup key. Same role at same company collapses to one hash
        even if it appears on multiple boards or the id churns."""
        basis = "|".join([
            _norm_text(self.company),
            _norm_text(self.title),
            _norm_text(self.location),
        ])
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    def age_hours(self, now: Optional[datetime] = None) -> Optional[float]:
        if self.posted_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        return (now - self.posted_at).total_seconds() / 3600.0

    def recency_bucket(self, now: Optional[datetime] = None) -> str:
        h = self.age_hours(now)
        if h is None:
            return "unknown"
        if h <= 24:
            return "24h"
        if h <= 48:
            return "48h"
        if h <= 72:
            return "72h"
        if h <= 168:
            return "7d"
        return "older"

    def to_row(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        d["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        d["content_hash"] = self.content_hash()
        return d
