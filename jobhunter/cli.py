"""Command-line entry point.

    python -m jobhunter.cli routine       # FULL daily run: search -> score -> shortlist -> report
    python -m jobhunter.cli search        # fetch all sources -> dedup -> store
    python -m jobhunter.cli score         # score unscored jobs against your profile
    python -m jobhunter.cli report        # print the latest daily report
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
        "push-profile": cmd_push_profile,
    }
    if cmd == "list":
        return cmd_list(argv[1] if len(argv) > 1 else None)
    if cmd == "test-send":
        return cmd_test_send(argv[1] if len(argv) > 1 else None)
    if cmd in dispatch:
        return dispatch[cmd]()
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
