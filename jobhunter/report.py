"""Stage 21: the daily report — top opportunities ranked by interview probability."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT
from .db import DB
from .profile import Profile


def build_report(db: DB, p: Profile, ingest, scorepass, outreach=None) -> str:
    now = datetime.now(timezone.utc)
    counts = db.count_by_status()
    top = db.shortlist(limit=15)

    L = []
    L.append(f"# Daily Job Report — {p.name}")
    L.append(f"_{now.strftime('%Y-%m-%d %H:%M UTC')}_\n")

    L.append("## Summary")
    L.append(f"- Sources: {ingest.sources_ok} ok, {ingest.sources_failed} failed")
    L.append(f"- New jobs ingested today: {ingest.jobs_new} (of {ingest.jobs_seen} postings seen)")
    L.append(f"- Scored this run: {scorepass.scored} | shortlisted: {scorepass.shortlisted} "
             f"| rejected: {scorepass.rejected} | LLM-refined: {scorepass.llm_refined}")
    scoring_mode = "Claude-refined" if scorepass.llm_refined else "heuristic only (no ANTHROPIC_API_KEY)"
    L.append(f"- Scoring mode: **{scoring_mode}**")
    L.append(f"- DB status counts: {counts}\n")

    L.append("## Top opportunities (ranked by interview probability)")
    if not top:
        L.append("_No shortlisted jobs yet. Add relevant company boards to config/sources.yaml "
                 "and/or widen target_roles in config/profile.yaml._\n")
    else:
        L.append("| # | P(interview) | Fit | Role | Company | Posted | Why |")
        L.append("|---|---|---|---|---|---|---|")
        for i, r in enumerate(top, 1):
            dims = json.loads(r["dimensions_json"] or "{}")
            posted = _age(r["posted_at"])
            why = (r["rationale"] or "")[:70]
            L.append(f"| {i} | {r['interview_prob']:.0%} | {r['score']:.0f} | "
                     f"{r['title'][:42]} | {r['company'][:16]} | {posted} | {why} |")
        L.append("")
        L.append("### Links")
        for i, r in enumerate(top, 1):
            L.append(f"{i}. [{r['title']}]({r['url']}) — {r['company']}")
        L.append("")

    if outreach is not None:
        L.append("## Outreach")
        L.append(f"- Mode: **{outreach.mode}** "
                 f"({'Claude finds contacts via Apollo MCP' if outreach.mode=='mcp' else 'contacts via configured API keys'})")
        L.append(f"- Shortlisted considered: {outreach.considered} | "
                 f"companies needing a contact: {outreach.need_contact}"
                 + (f" | resolved by code: {outreach.resolved_by_code}" if outreach.mode == 'code' else ""))
        if outreach.skipped:
            L.append("- Skips: " + ", ".join(f"{n}× {r}" for r, n in
                     sorted(outreach.skipped.items(), key=lambda x: -x[1])))
        L.append("- Next: Claude resolves contacts (Apollo MCP) → `build-tasks` → Claude writes "
                 "emails → `commit-drafts` validates + queues (nothing sent while autosend is off).")
        queued = db.emails_by_status("queued")
        if queued:
            L.append(f"\n### Drafted emails awaiting your review ({len(queued)})")
            for e in queued[:10]:
                L.append(f"- **{e['company']}** → {e['to_email']} · _{e['subject']}_")
        L.append("")

    if scorepass.reject_reasons:
        L.append("## Why jobs were rejected")
        for reason, n in sorted(scorepass.reject_reasons.items(), key=lambda x: -x[1]):
            L.append(f"- {n}× {reason}")
        L.append("")

    if ingest.failures:
        L.append("## Source failures (auto-skipped, non-fatal)")
        for s, e in ingest.failures:
            L.append(f"- {s}: {e}")
        L.append("")

    L.append("## Next actions")
    L.append("- Review the top opportunities above and confirm the ones worth outreach.")
    L.append("- Phase C (contact discovery + drafted emails) activates once Apollo creds land "
             "in `secrets/apollo_accounts.yaml` and `ANTHROPIC_API_KEY` is set.")
    L.append("- Emails will be **drafted into a review queue first** (autosend stays off in "
             "`config/outreach_rules.yaml` until you flip it).")
    return "\n".join(L)


def write_report(report: str) -> str:
    d = ROOT / "data" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    # filename without wall-clock RNG; date is fine for one run/day
    from datetime import datetime, timezone
    fname = datetime.now(timezone.utc).strftime("report-%Y-%m-%d.md")
    path = d / fname
    path.write_text(report)
    latest = d / "latest.md"
    latest.write_text(report)
    return str(path)


def _yn(v) -> str:
    return "✓" if v else "✗"


def _age(posted_at: str | None) -> str:
    if not posted_at:
        return "?"
    try:
        dt = datetime.fromisoformat(posted_at)
        h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return f"{h:.0f}h" if h < 72 else f"{h/24:.0f}d"
    except ValueError:
        return "?"
