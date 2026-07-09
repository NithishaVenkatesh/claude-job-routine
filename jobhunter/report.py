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


# --- machine-readable + dashboard outputs (AGENT_SPEC "Reports") -----------
def _top_rows(db: DB, limit: int = 50) -> list:
    rows = []
    for i, r in enumerate(db.shortlist(limit=limit), 1):
        rows.append({
            "rank": i,
            "interview_prob": round(float(r["interview_prob"] or 0), 3),
            "score": round(float(r["score"] or 0), 1),
            "title": r["title"], "company": r["company"],
            "location": r["location"] or "", "remote": bool(r["remote"]),
            "posted_at": r["posted_at"], "age": _age(r["posted_at"]),
            "source": r["source"], "url": r["url"],
            "rationale": r["rationale"] or "",
        })
    return rows


def build_report_data(db: DB, p: Profile, ingest, scorepass, outreach=None) -> dict:
    """One structured object all machine formats render from."""
    now = datetime.now(timezone.utc)
    top = _top_rows(db, limit=50)
    queued = [{"company": e["company"], "to": e["to_email"], "subject": e["subject"],
               "status": e["status"], "created_at": e["created_at"]}
              for e in db.emails_by_status("queued")]
    sent = [{"company": e["company"], "to": e["to_email"], "subject": e["subject"],
             "sent_at": e["sent_at"]} for e in db.emails_by_status("sent")]
    data = {
        "generated_at": now.isoformat(),
        "candidate": p.name,
        "summary": {
            "sources_ok": ingest.sources_ok, "sources_failed": ingest.sources_failed,
            "jobs_seen": ingest.jobs_seen, "jobs_new": ingest.jobs_new,
            "scored": scorepass.scored, "shortlisted": scorepass.shortlisted,
            "rejected": scorepass.rejected, "llm_refined": scorepass.llm_refined,
            "db_status_counts": db.count_by_status(),
            "emails_queued": len(queued), "emails_sent_total": len(sent),
            "emails_sent_today": db.emails_sent_today(),
        },
        "top_50": top,
        "top_20_highest_probability": top[:20],
        "reject_reasons": dict(sorted(scorepass.reject_reasons.items(), key=lambda x: -x[1])),
        "source_failures": [{"source": s, "error": e} for s, e in ingest.failures],
        "emails": {"queued": queued, "sent": sent[:50]},
    }
    if outreach is not None:
        data["outreach"] = {"mode": outreach.mode, "considered": outreach.considered,
                            "need_contact": outreach.need_contact,
                            "resolved_by_code": outreach.resolved_by_code,
                            "skipped": outreach.skipped}
    return data


def write_artifacts(data: dict) -> dict:
    """Write report.json / report.csv / dashboard.html next to the markdown report."""
    import csv
    d = ROOT / "data" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    paths = {}

    jp = d / "report.json"
    jp.write_text(json.dumps(data, indent=2))
    paths["json"] = str(jp)

    cp = d / "report.csv"
    cols = ["rank", "interview_prob", "score", "title", "company", "location",
            "remote", "posted_at", "age", "source", "url", "rationale"]
    with cp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in data["top_50"]:
            w.writerow({k: row.get(k, "") for k in cols})
    paths["csv"] = str(cp)

    hp = d / "dashboard.html"
    hp.write_text(_render_dashboard(data))
    paths["html"] = str(hp)
    return paths


def _render_dashboard(data: dict) -> str:
    """Self-contained HTML dashboard — no external assets, opens straight from disk."""
    from html import escape as esc
    s = data["summary"]

    def tile(label, value):
        return (f'<div class="tile"><div class="v">{esc(str(value))}</div>'
                f'<div class="l">{esc(label)}</div></div>')

    tiles = "".join([
        tile("new jobs today", s["jobs_new"]),
        tile("shortlisted", s["shortlisted"]),
        tile("rejected", s["rejected"]),
        tile("emails queued", s["emails_queued"]),
        tile("sent today", s["emails_sent_today"]),
        tile("sources ok", f'{s["sources_ok"]}/{s["sources_ok"] + s["sources_failed"]}'),
    ])

    job_rows = "".join(
        f'<tr><td>{r["rank"]}</td><td>{r["interview_prob"]:.0%}</td>'
        f'<td>{r["score"]:.0f}</td>'
        f'<td><a href="{esc(r["url"])}" target="_blank">{esc(r["title"][:70])}</a></td>'
        f'<td>{esc(r["company"][:24])}</td><td>{esc(r["age"])}</td>'
        f'<td>{esc(r["location"][:28])}</td><td>{esc(r["rationale"][:80])}</td></tr>'
        for r in data["top_50"])

    email_rows = "".join(
        f'<tr><td>{esc(e["company"] or "")}</td><td>{esc(e["to"] or "")}</td>'
        f'<td>{esc(e["subject"] or "")}</td><td>{esc(e["status"])}</td></tr>'
        for e in data["emails"]["queued"][:30])

    reject_rows = "".join(
        f'<tr><td>{n}</td><td>{esc(reason)}</td></tr>'
        for reason, n in list(data["reject_reasons"].items())[:15])

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Hunter — {esc(data["generated_at"][:10])}</title>
<style>
:root {{ color-scheme: light dark; --fg:#1a1a2e; --bg:#fafafa; --card:#fff; --mut:#667; --line:#e4e4ec; --acc:#3b5bdb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --fg:#e8e8f0; --bg:#111118; --card:#1c1c26; --mut:#99a; --line:#2c2c3a; --acc:#7c9aff; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:24px; font:14px/1.5 -apple-system,system-ui,sans-serif; color:var(--fg); background:var(--bg); }}
h1 {{ font-size:20px; margin:0 0 4px; }} h2 {{ font-size:15px; margin:28px 0 10px; }}
.sub {{ color:var(--mut); margin-bottom:20px; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:12px; }}
.tile {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }}
.tile .v {{ font-size:24px; font-weight:650; }} .tile .l {{ color:var(--mut); font-size:12px; }}
.wrap {{ overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:10px; }}
table {{ border-collapse:collapse; width:100%; min-width:720px; }}
th,td {{ text-align:left; padding:7px 10px; border-top:1px solid var(--line); white-space:nowrap; }}
th {{ border-top:none; color:var(--mut); font-weight:600; font-size:12px; }}
td:last-child {{ white-space:normal; min-width:200px; }}
a {{ color:var(--acc); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
</style></head><body>
<h1>Autonomous Job Hunter</h1>
<div class="sub">{esc(data["candidate"])} · generated {esc(data["generated_at"][:16].replace("T", " "))} UTC</div>
<div class="tiles">{tiles}</div>
<h2>Top opportunities (ranked by interview probability)</h2>
<div class="wrap"><table>
<tr><th>#</th><th>P(interview)</th><th>Fit</th><th>Role</th><th>Company</th><th>Posted</th><th>Location</th><th>Why</th></tr>
{job_rows or '<tr><td colspan="8">No shortlisted jobs.</td></tr>'}
</table></div>
<h2>Outreach queue</h2>
<div class="wrap"><table>
<tr><th>Company</th><th>To</th><th>Subject</th><th>Status</th></tr>
{email_rows or '<tr><td colspan="4">Queue empty.</td></tr>'}
</table></div>
<h2>Top rejection reasons</h2>
<div class="wrap"><table>
<tr><th>Count</th><th>Reason</th></tr>
{reject_rows or '<tr><td colspan="2">None.</td></tr>'}
</table></div>
</body></html>"""


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
