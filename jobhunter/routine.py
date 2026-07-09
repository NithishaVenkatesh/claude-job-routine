"""The daily routine: the whole Phase A+B pipeline, checkpoint-resumable.

    ingest -> score -> shortlist -> report

Each stage is idempotent and logged, so a crash mid-run resumes cleanly on the next
invocation (BLUEPRINT §3.1/§8). Contact-discovery + outreach (Phase C) are wired as
explicit stubs that stay OFF until Apollo creds + ANTHROPIC_API_KEY are provided and
autosend is enabled in outreach_rules.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .db import DB
from .ingest import run_ingest
from .profile import load_profile, Profile
from .score import heuristic_score
from . import llm as llm_mod

SHORTLIST_THRESHOLD = 0.0   # jobs with interview_prob above this become 'shortlisted'
LLM_REFINE_TOP_N = 40       # only the best heuristic matches get (costly) LLM scoring
OUTREACH_TOP_N = 25         # only pursue contacts/emails for the very top matches


@dataclass
class ScorePass:
    scored: int = 0
    shortlisted: int = 0
    rejected: int = 0
    llm_refined: int = 0
    reject_reasons: dict = field(default_factory=dict)


def run_scoring(db: DB, p: Profile, use_llm: bool = True) -> ScorePass:
    """Score every unscored job heuristically; refine top-N with the LLM if available."""
    res = ScorePass()
    survivors = []   # (interview_prob, job) for LLM refinement
    batch = []       # (Score, status) for one bulk write

    for row in db.unscored_jobs():
        job = _row_to_job(row)
        s = heuristic_score(job, p)
        res.scored += 1
        if s.rejected:
            res.rejected += 1
            res.reject_reasons[s.reject_reason] = res.reject_reasons.get(s.reject_reason, 0) + 1
            batch.append((s, "rejected"))
        else:
            status = "shortlisted" if s.interview_prob > SHORTLIST_THRESHOLD else "scored"
            batch.append((s, status))
            if status == "shortlisted":
                res.shortlisted += 1
                survivors.append((s.interview_prob, job))

    db.bulk_save_scores(batch)   # one bulk write instead of thousands of round trips

    # LLM refinement of the strongest survivors only (cost control)
    if use_llm and llm_mod.available() and survivors:
        survivors.sort(key=lambda x: x[0], reverse=True)
        for _, job in survivors[:LLM_REFINE_TOP_N]:
            ls = llm_mod.llm_score(job, p)
            if ls is None:
                continue
            res.llm_refined += 1
            new_status = "rejected" if ls.rejected else "shortlisted"
            db.save_score(ls, new_status)
            if ls.rejected:
                res.shortlisted -= 1
                res.rejected += 1
                res.reject_reasons[ls.reject_reason or "llm reject"] = \
                    res.reject_reasons.get(ls.reject_reason or "llm reject", 0) + 1
    return res


def _row_to_job(row):
    from .models import JobPosting
    from datetime import datetime
    posted = None
    if row["posted_at"]:
        try:
            posted = datetime.fromisoformat(row["posted_at"])
        except ValueError:
            posted = None
    return JobPosting(
        source=row["source"], source_company=row["source_company"],
        external_id=row["external_id"], title=row["title"], company=row["company"],
        url=row["url"], location=row["location"] or "", remote=bool(row["remote"]),
        employment_type=row["employment_type"] or "", description=row["description"] or "",
        posted_at=posted,
    )


def run_daily(db: DB | None = None, use_llm: bool = True) -> str:
    """Full morning routine. Returns the rendered daily report (also written to disk)."""
    from .report import build_report, write_report

    close = False
    if db is None:
        db, close = DB(), True
    p = load_profile()

    run_id = db.start_run()
    stage_status = {}

    # Stage: ingest (search + recency + dedup)
    ing = run_ingest(db)
    stage_status["ingest"] = f"{ing.sources_ok} ok / {ing.sources_failed} failed, +{ing.jobs_new} new"

    # Stage: score + shortlist (match + interview-probability ranking)
    sp = run_scoring(db, p, use_llm=use_llm)
    stage_status["score"] = f"{sp.scored} scored, {sp.shortlisted} shortlisted, {sp.rejected} rejected"

    # Stage: prepare outreach. In MCP mode this writes data/contact_tasks.json (companies
    # needing a contact) for Claude to resolve via Apollo MCP; contact discovery + email
    # writing are done by the routine agent, not this code.
    from .outreach import prepare
    pr = prepare(db, p)
    stage_status["prepare"] = (f"mode={pr.mode}, considered={pr.considered}, "
                               f"need_contact={pr.need_contact}, by_code={pr.resolved_by_code}")

    # Stage: report (markdown + JSON/CSV/HTML dashboard)
    from .report import build_report_data, write_artifacts
    report = build_report(db, p, ing, sp, pr)
    path = write_report(report)
    artifacts = write_artifacts(build_report_data(db, p, ing, sp, pr))
    stage_status["report"] = path
    stage_status["artifacts"] = artifacts

    db.finish_run(run_id, stage_status, report[:2000])
    db.log("routine", "run", str(run_id), "done", stage_status)

    if close:
        db.close()
    return report
