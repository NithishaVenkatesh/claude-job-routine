"""Source interface + shared HTTP with retry/backoff (BLUEPRINT §8)."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..models import JobPosting

USER_AGENT = "jobhunter/0.1 (personal job search; contact via email)"
_TRANSIENT = {429, 500, 502, 503, 504}


class SourceError(Exception):
    """Raised when a source fails after retries. Ingest catches this per-source
    so one broken board never kills the whole run."""


def get_json(url: str, *, params: dict | None = None, method: str = "GET",
             json_body: dict | None = None, retries: int = 3, timeout: float = 20.0):
    """HTTP with exponential backoff + jitter on transient failures."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}) as c:
                resp = c.request(method, url, params=params, json=json_body)
            if resp.status_code in _TRANSIENT:
                raise SourceError(f"transient {resp.status_code} from {url}")
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, SourceError) as e:
            last = e
            if attempt < retries - 1:
                # 0.5, 1.0, 2.0 ... with a fixed jitter (no RNG: deterministic for resume)
                time.sleep(0.5 * (2 ** attempt) + 0.1 * (attempt + 1))
    raise SourceError(f"failed after {retries} tries: {url}: {last}")


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def parse_epoch_ms(v) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(v) / 1000.0, tz=timezone.utc)
    except (ValueError, TypeError):
        return None


def looks_remote(*fields: str) -> bool:
    blob = " ".join(f.lower() for f in fields if f)
    return "remote" in blob


class Source:
    """Base class. Subclasses implement fetch() → list[JobPosting]."""

    type_name: str = "base"

    def __init__(self, company: str):
        self.company = company  # board token / slug

    def fetch(self) -> list[JobPosting]:
        raise NotImplementedError
