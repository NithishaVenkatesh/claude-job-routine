"""Command-line entry point.

    python -m jobhunter.cli routine       # FULL daily run: search -> score -> shortlist -> report
    python -m jobhunter.cli search        # fetch all sources -> dedup -> store
    python -m jobhunter.cli score         # score unscored jobs against your profile
    python -m jobhunter.cli report        # print the latest daily report
    python -m jobhunter.cli export-rerank # top survivors -> rerank_tasks.json for the agent
    python -m jobhunter.cli apply-llm-scores [path]  # upsert agent's refined_scores.json
    python -m jobhunter.cli prepare       # refresh contact_tasks.json from the shortlist
    python -m jobhunter.cli build-tasks   # turn Claude's found_contacts.json into email tasks
    python -m jobhunter.cli commit-drafts # validate + queue the emails Claude wrote
    python -m jobhunter.cli test-send [to] # send ONE test email to verify sending works
    python -m jobhunter.cli queue         # show drafted emails awaiting review
    python -m jobhunter.cli list          # show stored jobs (newest first)
    python -m jobhunter.cli stats         # quick counts
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from .db import DB
from .ingest import run_ingest


def _fmt_age(posted_at: str | None) -> str:
    if not posted_at:
        return "  ?  "
    try:
        dt = datetime.fromisoformat(posted_at)
        h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return f"{h:4.0f}h" if h < 168 else f"{h/24:3.0f}d"
    except ValueError:
        return "  ?  "


def cmd_search() -> int:
    db = DB()
    run_id = db.start_run()
    rep = run_ingest(db)
    db.finish_run(run_id, {"ingest": "done"}, rep.render())
    print(rep.render())
    db.close()
    return 0


def cmd_list(bucket: str | None = None) -> int:
    db = DB()
    rows = db.all_jobs()
    shown = 0
    for r in rows:
        age = _fmt_age(r["posted_at"])
        if bucket:
            # crude filter using the printed age; good enough for Phase A browsing
            pass
        remote = "REMOTE" if r["remote"] else "     "
        print(f"[{age}] {remote}  {r['company'][:18]:18}  {r['title'][:60]:60}  {r['url']}")
        shown += 1
        if shown >= 100:
            print("... (truncated at 100)")
            break
    print(f"\n{len(rows)} jobs stored.")
    db.close()
    return 0


def cmd_stats() -> int:
    db = DB()
    rows = db.all_jobs()
    by_source: dict[str, int] = {}
    for r in rows:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    print(f"total jobs: {len(rows)}")
    for s, n in sorted(by_source.items()):
        print(f"  {s}: {n}")
    db.close()
    return 0


def cmd_score() -> int:
    from .profile import load_profile
    from .routine import run_scoring
    db = DB()
    p = load_profile()
    sp = run_scoring(db, p)
    print(f"scored={sp.scored} shortlisted={sp.shortlisted} rejected={sp.rejected} "
          f"llm_refined={sp.llm_refined}")
    if sp.reject_reasons:
        print("reject reasons:")
        for r, n in sorted(sp.reject_reasons.items(), key=lambda x: -x[1])[:8]:
            print(f"  {n:5d}  {r}")
    db.close()
    return 0


def cmd_routine() -> int:
    from .routine import run_daily
    report = run_daily()
    print(report)
    return 0


def cmd_report() -> int:
    from .config import ROOT
    latest = ROOT / "data" / "reports" / "latest.md"
    print(latest.read_text() if latest.exists() else "No report yet. Run: routine")
    return 0


def cmd_queue() -> int:
    db = DB()
    rows = db.emails_by_status("queued")
    if not rows:
        print("No queued drafts. Run: routine  (drafts appear here in shadow mode)")
        db.close()
        return 0
    for e in rows:
        print("=" * 70)
        print(f"To: {e['to_email']}  |  {e['company']}  |  {e['template_class']}")
        print(f"Subject: {e['subject']}")
        print(f"\n{e['body']}\n")
    print("=" * 70)
    print(f"{len(rows)} draft(s) queued (shadow mode — nothing sent).")
    db.close()
    return 0


def cmd_test_send(to: str | None = None) -> int:
    from . import send as send_mod
    to = to or "ajayaditya.dev@gmail.com"
    if not send_mod.sender_ready():
        print("No sender configured. Set GMAIL_SENDER and GMAIL_APP_PASSWORD in .env "
              "(Gmail App Password), then re-run.")
        return 1
    subject = "[jobhunter] test send — pipeline check"
    body = ("This is a test email from Nithisha's automated job-outreach routine, confirming "
            "that the sending path works end-to-end.\n\nIf you received this, sending is wired "
            "correctly.\n\n— jobhunter test-send")
    ok, info = send_mod.send_email(to, subject, body)
    print(f"SENT -> {to} ({info})" if ok else f"FAILED -> {to}: {info}")
    return 0 if ok else 1


def cmd_ingest_leads() -> int:
    """Ingest jobs the agent found by web-searching (data/web_leads.json), score them,
    and refresh contact tasks so fresh finds enter today's outreach."""
    import json
    from .config import ROOT
    from .models import JobPosting
    from .profile import load_profile
    from .routine import run_scoring
    from .outreach import prepare

    leads_path = ROOT / "data" / "web_leads.json"
    if not leads_path.exists():
        print("no data/web_leads.json — nothing to ingest")
        return 0
    leads = json.loads(leads_path.read_text()).get("jobs", [])
    db = DB()
    jobs = []
    for j in leads:
        if not j.get("title") or not j.get("company") or not j.get("url"):
            continue
        posted = None
        if j.get("posted_at"):
            try:
                posted = datetime.fromisoformat(str(j["posted_at"]).replace("Z", "+00:00"))
            except ValueError:
                posted = None
        jobs.append(JobPosting(
            source="web_lead", source_company="agent-search", external_id=j["url"][:200],
            title=j["title"][:150], company=j["company"][:80], url=j["url"],
            location=j.get("location", "") or "", remote="remote" in (j.get("location", "") or "").lower(),
            description=(j.get("description") or "")[:4000],
            # honest recency: undated leads stay undated (scored via the 'unknown'
            # bucket, 0.5 boost) instead of being faked into the 24h bucket (1.0).
            # The DB's first_seen column is the recency handle for undated leads.
            posted_at=posted,
        ))
    new_jobs, seen = db.bulk_upsert_jobs(jobs)
    p = load_profile()
    import os
    use_llm = os.environ.get("USE_LLM_SCORING", "0") == "1"
    sp = run_scoring(db, p, use_llm=use_llm)
    pr = prepare(db, p)   # refresh contact tasks incl. fresh leads
    print(f"leads_in={len(leads)} new={len(new_jobs)} already_known={seen} "
          f"scored={sp.scored} shortlisted_now={sp.shortlisted} contact_tasks={pr.need_contact}")
    db.close()
    return 0


def cmd_state_pull(path: str | None = None) -> int:
    from .ghstate import pull_state, DEFAULT_IN
    db = DB()
    counts = pull_state(db, path or DEFAULT_IN)
    print("state restored:", counts)
    if counts.get("_note") or counts.get("error") or not any(
            counts.get(k) for k in ("seen", "contacts", "emails")):
        # loud, un-missable failure — silent memory rot caused weeks of repeated leads
        print("⚠️  MEMORY UNAVAILABLE — no prior state restored. Leads WILL repeat and "
              "contact quota may be wasted on companies already handled. Fix the Drive "
              "connector. Put this warning at the TOP of the final report.")
    db.close()
    return 0


def cmd_state_push(path: str | None = None) -> int:
    from .ghstate import push_state, DEFAULT_OUT
    db = DB()
    size = push_state(db, path or DEFAULT_OUT)
    print(f"state written to {path or DEFAULT_OUT} ({size} bytes)")
    db.close()
    return 0


def cmd_push_profile() -> int:
    from .config import ROOT
    db = DB()
    if db.backend != "pg":
        print("Set DATABASE_URL (Neon) first — nothing to push to in SQLite mode.")
        db.close(); return 1
    files = {"profile_yaml": "config/profile.yaml",
             "resume": "data/profile/resume.txt",
             "context": "data/profile/context.md"}
    n = 0
    for key, path in files.items():
        fp = ROOT / path
        if fp.exists():
            db.set_profile_doc(key, fp.read_text())
            print(f"pushed {key} ({len(fp.read_text())} chars)")
            n += 1
    db.close()
    print(f"{n} profile doc(s) now in Neon — repo can safely drop them.")
    return 0


def cmd_export_rerank() -> int:
    """Write data/rerank_tasks.json: the top heuristic survivors, for the routine agent
    to re-score with a calibrated interview probability. This replaces the llm_score()
    HTTP path, which the cloud sandbox blocks — the agent IS the LLM in the loop."""
    import json
    import os
    from .config import ROOT
    from .profile import load_profile
    top_n = int(os.environ.get("RERANK_TOP_N", "40"))
    db = DB()
    p = load_profile()
    rows = db.shortlist(limit=top_n)
    tasks = [{"job_hash": r["content_hash"], "title": r["title"], "company": r["company"],
              "location": r["location"], "url": r["url"],
              "description": (r["description"] or "")[:1200],
              "heuristic_prob": r["interview_prob"], "heuristic_rationale": r["rationale"]}
             for r in rows]
    out = ROOT / "data" / "rerank_tasks.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "profile": p.summary_for_llm(),
        "instructions": (
            "You (Claude) re-score each job on ONE metric: this candidate's probability "
            "of getting an interview if they apply — role fit, skill match, seniority fit, "
            "likely competition. Never reward brand names; favor roles where THIS candidate "
            "stands out. Be honest and calibrated. Reject weak fits the heuristic missed "
            "(hidden seniority, wrong domain, non-engineering). Write data/refined_scores.json "
            "as {\"scores\":[{\"job_hash\":..., \"interview_prob\":<0.0-1.0>, "
            "\"score\":<0-100>, \"reject\":<true|false>, \"reject_reason\":<str|null>, "
            "\"rationale\":<one concrete sentence>}]} — one entry per task, then run: "
            "python3 -m jobhunter.cli apply-llm-scores"),
        "tasks": tasks,
    }, indent=2))
    print(f"rerank_tasks={len(tasks)} -> {out}")
    db.close()
    return 0


def cmd_apply_llm_scores(path: str | None = None) -> int:
    """Upsert the agent's calibrated scores (data/refined_scores.json) into the DB.
    Shortlist ordering uses interview_prob, so refined scores take effect immediately."""
    import json
    from .config import ROOT
    from .score import Score
    fp = ROOT / (path or "data/refined_scores.json")
    if not fp.exists():
        print(f"no {fp.name} — nothing to apply")
        return 0
    items = json.loads(fp.read_text()).get("scores", [])
    db = DB()
    applied = rejected = skipped = 0
    for it in items:
        jh = it.get("job_hash")
        try:
            prob = max(0.0, min(1.0, float(it.get("interview_prob", 0))))
            score_val = max(0.0, min(100.0, float(it.get("score", prob * 100))))
        except (TypeError, ValueError):
            skipped += 1
            continue
        if not jh:
            skipped += 1
            continue
        reject = bool(it.get("reject"))
        s = Score(content_hash=jh, score=score_val, interview_prob=prob,
                  rationale=str(it.get("rationale", ""))[:400],
                  reject_reason=(str(it.get("reject_reason") or "agent reject")
                                 if reject else None),
                  model="llm:agent")
        db.save_score(s, "rejected" if reject else "shortlisted")
        applied += 1
        rejected += int(reject)
    print(f"refined_scores applied={applied} rejected_by_agent={rejected} skipped={skipped}")
    db.close()
    return 0


def cmd_prepare() -> int:
    """Re-run outreach.prepare() standalone — refreshes data/contact_tasks.json (used
    after apply-llm-scores so contact tasks reflect the refined ranking)."""
    from .profile import load_profile
    from .outreach import prepare
    db = DB()
    pr = prepare(db, load_profile())
    print(f"mode={pr.mode} considered={pr.considered} need_contact={pr.need_contact} "
          f"by_code={pr.resolved_by_code}")
    if pr.skipped:
        for r, n in sorted(pr.skipped.items(), key=lambda x: -x[1]):
            print(f"  {n}x  {r}")
    db.close()
    return 0


def cmd_build_tasks() -> int:
    from .profile import load_profile
    from .outreach import build_tasks
    db = DB()
    res = build_tasks(db, load_profile())
    print(f"contacts_in={res.contacts_in} stored={res.stored} tasks_written={res.tasks_written}")
    if res.skipped:
        for r, n in sorted(res.skipped.items(), key=lambda x: -x[1]):
            print(f"  {n}x  {r}")
    db.close()
    return 0


def cmd_commit_drafts() -> int:
    from .profile import load_profile
    from .outreach import commit
    db = DB()
    p = load_profile()
    res = commit(db, p)
    print(f"mode={res.mode} drafts_read={res.drafts_read} queued={res.queued} "
          f"sent={res.sent} failed={res.failed}")
    if res.held:
        for r, n in sorted(res.held.items(), key=lambda x: -x[1]):
            print(f"  {n}x  {r}")
    db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "routine"
    dispatch = {
        "routine": cmd_routine, "search": cmd_search, "score": cmd_score,
        "report": cmd_report, "stats": cmd_stats, "queue": cmd_queue,
        "build-tasks": cmd_build_tasks, "commit-drafts": cmd_commit_drafts,
        "push-profile": cmd_push_profile, "ingest-leads": cmd_ingest_leads,
        "state-pull": cmd_state_pull, "state-push": cmd_state_push,
        "export-rerank": cmd_export_rerank, "prepare": cmd_prepare,
    }
    if cmd == "apply-llm-scores":
        return cmd_apply_llm_scores(argv[1] if len(argv) > 1 else None)
    if cmd == "list":
        return cmd_list(argv[1] if len(argv) > 1 else None)
    if cmd == "test-send":
        return cmd_test_send(argv[1] if len(argv) > 1 else None)
    if cmd == "state-pull":
        return cmd_state_pull(argv[1] if len(argv) > 1 else None)
    if cmd == "state-push":
        return cmd_state_push(argv[1] if len(argv) > 1 else None)
    if cmd in dispatch:
        return dispatch[cmd]()
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
