"""Stage 3+4 (search + recency): run every configured source, normalize, dedup,
store. Fault-tolerant: a failing source is logged and skipped, never fatal.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import load_sources
from .db import DB
from .sources import build
from .sources.base import SourceError


@dataclass
class IngestReport:
    sources_ok: int = 0
    sources_failed: int = 0
    jobs_seen: int = 0          # total postings returned
    jobs_new: int = 0           # newly inserted (unique)
    failures: list[tuple[str, str]] = field(default_factory=list)  # (source, error)
    by_bucket: dict[str, int] = field(default_factory=dict)

    def render(self) -> str:
        lines = [
            "=== Ingest report ===",
            f"sources ok/failed : {self.sources_ok}/{self.sources_failed}",
            f"postings returned : {self.jobs_seen}",
            f"new unique jobs   : {self.jobs_new}",
            f"recency (new)     : " + ", ".join(f"{k}={v}" for k, v in sorted(self.by_bucket.items())),
        ]
        if self.failures:
            lines.append("failures:")
            lines += [f"  - {s}: {e}" for s, e in self.failures]
        return "\n".join(lines)


def run_ingest(db: DB, sources: list[dict] | None = None) -> IngestReport:
    sources = sources if sources is not None else load_sources()
    rep = IngestReport()
    collected = []
    for spec in sources:
        stype, company = spec.get("type"), spec.get("company")
        if not stype or not company:
            continue
        label = f"{stype}:{company}"
        try:
            jobs = build(stype, company).fetch()
        except (SourceError, ValueError, Exception) as e:  # never let one source kill the run
            rep.sources_failed += 1
            rep.failures.append((label, str(e)[:200]))
            db.log("ingest", "source", label, "failed", {"error": str(e)[:500]})
            continue

        rep.sources_ok += 1
        rep.jobs_seen += len(jobs)
        collected.extend(jobs)
        db.log("ingest", "source", label, "ok", {"returned": len(jobs)})

    # single bulk write (fast on Neon) instead of per-row round trips
    new_jobs, _seen = db.bulk_upsert_jobs(collected)
    rep.jobs_new = len(new_jobs)
    for job in new_jobs:
        b = job.recency_bucket()
        rep.by_bucket[b] = rep.by_bucket.get(b, 0) + 1
    return rep
