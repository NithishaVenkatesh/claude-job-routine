"""Data access. Backed by Store (Postgres when DATABASE_URL is set, else SQLite).

Every external object has an idempotency key so re-runs never duplicate. Nothing is
hard-deleted; state lives in status columns. Same code path works locally and in the
cloud — only the storage backend changes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .models import JobPosting
from .store import Store, INTEGRITY_ERRORS

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    content_hash   TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    source_company TEXT NOT NULL,
    external_id    TEXT NOT NULL,
    title          TEXT NOT NULL,
    company        TEXT NOT NULL,
    url            TEXT NOT NULL,
    location       TEXT,
    remote         INTEGER DEFAULT 0,
    employment_type TEXT,
    description    TEXT,
    posted_at      TEXT,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'new'
);
CREATE TABLE IF NOT EXISTS job_scores (
    content_hash    TEXT PRIMARY KEY REFERENCES jobs(content_hash),
    score           REAL,
    interview_prob  REAL,
    dimensions_json TEXT,
    rationale       TEXT,
    reject_reason   TEXT,
    model           TEXT,
    scored_at       TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id          {pk},
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    stage_status_json TEXT,
    summary     TEXT
);
CREATE TABLE IF NOT EXISTS actions_log (
    id          {pk},
    ts          TEXT NOT NULL,
    stage       TEXT,
    entity_type TEXT,
    entity_id   TEXT,
    action      TEXT,
    detail_json TEXT
);
CREATE TABLE IF NOT EXISTS companies (
    id            {pk},
    name          TEXT NOT NULL UNIQUE,
    domain        TEXT,
    research_json TEXT,
    researched_at TEXT
);
CREATE TABLE IF NOT EXISTS contacts (
    id             {pk},
    company        TEXT NOT NULL,
    full_name      TEXT,
    title          TEXT,
    linkedin_url   TEXT,
    email          TEXT,
    source_provider TEXT,
    confidence     REAL,
    verification_status TEXT,
    retrieved_at   TEXT,
    UNIQUE(email, company)
);
CREATE TABLE IF NOT EXISTS emails (
    id            {pk},
    job_hash      TEXT REFERENCES jobs(content_hash),
    contact_id    INTEGER REFERENCES contacts(id),
    company       TEXT,
    to_email      TEXT,
    template_class TEXT,
    subject       TEXT,
    body          TEXT,
    hook_note     TEXT,
    status        TEXT NOT NULL DEFAULT 'draft',
    safety_json   TEXT,
    message_id    TEXT,
    error         TEXT,
    created_at    TEXT,
    sent_at       TEXT,
    UNIQUE(contact_id, job_hash)
);
CREATE TABLE IF NOT EXISTS suppression (
    email      TEXT PRIMARY KEY,
    reason     TEXT,
    added_at   TEXT
);
CREATE TABLE IF NOT EXISTS profile_docs (
    key        TEXT PRIMARY KEY,          -- 'profile_yaml' | 'resume' | 'context'
    content    TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS provider_usage (
    provider   TEXT,
    account    TEXT,
    period     TEXT,
    used       INTEGER DEFAULT 0,
    cap        INTEGER,
    updated_at TEXT,
    PRIMARY KEY (provider, account, period)
);
CREATE INDEX IF NOT EXISTS idx_jobs_status   ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_posted   ON jobs(posted_at);
CREATE INDEX IF NOT EXISTS idx_emails_status ON emails(status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DB:
    def __init__(self, url: str | None = None, sqlite_path: str = "data/jobhunter.db"):
        self.s = Store(url=url, sqlite_path=sqlite_path)
        self.s.executescript(SCHEMA)

    @property
    def backend(self) -> str:
        return self.s.kind

    # ---- jobs -----------------------------------------------------------
    def upsert_job(self, job: JobPosting) -> str:
        row = job.to_row()
        h = row["content_hash"]
        now = _now()
        if self.s.queryone("SELECT content_hash FROM jobs WHERE content_hash=?", (h,)):
            self.s.execute("UPDATE jobs SET last_seen=? WHERE content_hash=?", (now, h))
            return "seen"
        self.s.execute(
            """INSERT INTO jobs (content_hash, source, source_company, external_id,
                 title, company, url, location, remote, employment_type, description,
                 posted_at, first_seen, last_seen, status)
               VALUES (:content_hash, :source, :source_company, :external_id,
                 :title, :company, :url, :location, :remote, :employment_type, :description,
                 :posted_at, :first_seen, :last_seen, 'new')""",
            {**row, "remote": int(row["remote"]), "first_seen": now, "last_seen": now})
        return "inserted"

    def existing_hashes(self) -> set:
        return {r["content_hash"] for r in self.s.query("SELECT content_hash FROM jobs")}

    def bulk_upsert_jobs(self, jobs):
        """Fast path: one bulk INSERT for all new jobs. Returns (new_jobs, seen_count)."""
        existing = self.existing_hashes()
        now = _now()
        new_jobs, new_rows, seen = [], [], 0
        for job in jobs:
            row = job.to_row()
            h = row["content_hash"]
            if h in existing:
                seen += 1
                continue
            existing.add(h)  # dedup within this batch too
            new_jobs.append(job)
            new_rows.append((row["content_hash"], row["source"], row["source_company"],
                             row["external_id"], row["title"], row["company"], row["url"],
                             row["location"], int(row["remote"]), row["employment_type"],
                             row["description"], row["posted_at"], now, now))
        if new_rows:
            self.s.copy_insert(
                "jobs", ["content_hash", "source", "source_company", "external_id", "title",
                         "company", "url", "location", "remote", "employment_type",
                         "description", "posted_at", "first_seen", "last_seen"], new_rows)
        return new_jobs, seen

    def bulk_update_job_status(self, pairs):
        """pairs: list of (content_hash, status). One statement on Postgres."""
        if not pairs:
            return
        if self.s.kind == "pg":
            self.s.execute(
                """UPDATE jobs SET status = d.status
                   FROM unnest(?::text[], ?::text[]) AS d(content_hash, status)
                   WHERE jobs.content_hash = d.content_hash""",
                ([h for h, _ in pairs], [s for _, s in pairs]))
        else:
            self.s.executemany("UPDATE jobs SET status=? WHERE content_hash=?",
                               [(s, h) for h, s in pairs])

    def bulk_save_scores(self, items):
        """items: list of (Score, new_status). Heuristic pass only writes NEW scores
        (unscored jobs), so COPY is safe (no conflicts)."""
        if not items:
            return
        now = _now()
        score_rows = [(s.content_hash, s.score, s.interview_prob, json.dumps(s.dimensions),
                       s.rationale, s.reject_reason, s.model, now) for s, _ in items]
        self.s.copy_insert(
            "job_scores", ["content_hash", "score", "interview_prob", "dimensions_json",
                           "rationale", "reject_reason", "model", "scored_at"], score_rows)
        self.bulk_update_job_status([(s.content_hash, status) for s, status in items])

    def jobs_by_status(self, status: str):
        return self.s.query("SELECT * FROM jobs WHERE status=? ORDER BY posted_at DESC", (status,))

    def all_jobs(self):
        return self.s.query("SELECT * FROM jobs ORDER BY posted_at DESC")

    def unscored_jobs(self):
        return self.s.query(
            """SELECT j.* FROM jobs j
               LEFT JOIN job_scores s ON s.content_hash = j.content_hash
               WHERE s.content_hash IS NULL""")

    # ---- scoring --------------------------------------------------------
    def save_score(self, score, new_status: str):
        self.s.execute(
            """INSERT INTO job_scores
                 (content_hash, score, interview_prob, dimensions_json, rationale,
                  reject_reason, model, scored_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(content_hash) DO UPDATE SET
                 score=excluded.score, interview_prob=excluded.interview_prob,
                 dimensions_json=excluded.dimensions_json, rationale=excluded.rationale,
                 reject_reason=excluded.reject_reason, model=excluded.model,
                 scored_at=excluded.scored_at""",
            (score.content_hash, score.score, score.interview_prob,
             json.dumps(score.dimensions), score.rationale, score.reject_reason,
             score.model, _now()))
        self.s.execute("UPDATE jobs SET status=? WHERE content_hash=?",
                       (new_status, score.content_hash))

    def shortlist(self, limit: int = 50, min_prob: float = 0.0):
        return self.s.query(
            """SELECT j.*, s.score, s.interview_prob, s.rationale, s.dimensions_json, s.model
               FROM jobs j JOIN job_scores s ON s.content_hash = j.content_hash
               WHERE j.status='shortlisted' AND s.interview_prob >= ?
               ORDER BY s.interview_prob DESC, s.score DESC
               LIMIT ?""", (min_prob, limit))

    def get_interview_prob(self, content_hash: str):
        r = self.s.queryone("SELECT interview_prob FROM job_scores WHERE content_hash=?",
                            (content_hash,))
        return r["interview_prob"] if r else None

    def count_by_status(self) -> dict:
        rows = self.s.query("SELECT status, COUNT(*) AS c FROM jobs GROUP BY status")
        return {r["status"]: r["c"] for r in rows}

    # ---- companies / research ------------------------------------------
    def get_company_research(self, name: str):
        r = self.s.queryone("SELECT research_json, researched_at FROM companies WHERE name=?", (name,))
        if not r or not r["research_json"]:
            return None
        return {"research": json.loads(r["research_json"]), "researched_at": r["researched_at"]}

    def save_company_research(self, name: str, domain: str, research: dict):
        self.s.execute(
            """INSERT INTO companies (name, domain, research_json, researched_at)
               VALUES (?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 domain=excluded.domain, research_json=excluded.research_json,
                 researched_at=excluded.researched_at""",
            (name, domain, json.dumps(research), _now()))

    # ---- contacts -------------------------------------------------------
    def save_contact(self, c: dict):
        try:
            return self.s.insert(
                """INSERT INTO contacts (company, full_name, title, linkedin_url, email,
                     source_provider, confidence, verification_status, retrieved_at)
                   VALUES (:company,:full_name,:title,:linkedin_url,:email,
                     :source_provider,:confidence,:verification_status,:retrieved_at)""",
                {**c, "retrieved_at": _now()})
        except INTEGRITY_ERRORS:
            r = self.s.queryone("SELECT id FROM contacts WHERE email=? AND company=?",
                                (c.get("email"), c.get("company")))
            return r["id"] if r else None

    # ---- emails ---------------------------------------------------------
    def email_exists(self, contact_id: int, job_hash: str) -> bool:
        return self.s.queryone("SELECT 1 FROM emails WHERE contact_id=? AND job_hash=?",
                               (contact_id, job_hash)) is not None

    def job_has_email(self, job_hash: str) -> bool:
        """Checkpoint marker: this job already produced at least one draft/queued/sent
        email (this run or restored prior-run state) — outreach for it is done."""
        return self.s.queryone("SELECT 1 FROM emails WHERE job_hash=?",
                               (job_hash,)) is not None

    def save_email(self, e: dict):
        try:
            return self.s.insert(
                """INSERT INTO emails (job_hash, contact_id, company, to_email, template_class,
                     subject, body, hook_note, status, safety_json, message_id, error,
                     created_at, sent_at)
                   VALUES (:job_hash,:contact_id,:company,:to_email,:template_class,
                     :subject,:body,:hook_note,:status,:safety_json,:message_id,:error,
                     :created_at,:sent_at)""",
                {"created_at": _now(), "sent_at": None, "message_id": None, "error": None,
                 "safety_json": json.dumps(e.get("safety", {})), **e})
        except INTEGRITY_ERRORS:
            return None

    def update_email(self, email_id: int, **fields):
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.s.execute(f"UPDATE emails SET {cols} WHERE id=?", (*fields.values(), email_id))

    def emails_sent_today(self) -> int:
        r = self.s.queryone(
            "SELECT COUNT(*) AS c FROM emails WHERE status='sent' AND substr(sent_at,1,10)=?",
            (_now()[:10],))
        return r["c"] if r else 0

    def emails_by_status(self, status: str):
        return self.s.query("SELECT * FROM emails WHERE status=? ORDER BY created_at DESC", (status,))

    def company_email_count_today(self, company: str) -> int:
        r = self.s.queryone(
            """SELECT COUNT(*) AS c FROM emails WHERE company=?
               AND status IN ('sent','queued','approved') AND substr(created_at,1,10)=?""",
            (company, _now()[:10]))
        return r["c"] if r else 0

    # ---- suppression ----------------------------------------------------
    def is_suppressed(self, email: str) -> bool:
        return self.s.queryone("SELECT 1 FROM suppression WHERE email=?", (email,)) is not None

    def suppress(self, email: str, reason: str):
        self.s.execute(
            "INSERT INTO suppression (email, reason, added_at) VALUES (?,?,?) ON CONFLICT DO NOTHING",
            (email, reason, _now()))

    # ---- profile docs (so PII lives in Neon, not the repo) --------------
    def set_profile_doc(self, key: str, content: str):
        self.s.execute(
            """INSERT INTO profile_docs (key, content, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET content=excluded.content,
                 updated_at=excluded.updated_at""",
            (key, content, _now()))

    def get_profile_doc(self, key: str):
        r = self.s.queryone("SELECT content FROM profile_docs WHERE key=?", (key,))
        return r["content"] if r else None

    # ---- audit / runs ---------------------------------------------------
    def log(self, stage: str, entity_type: str, entity_id: str, action: str, detail: dict | None = None):
        self.s.execute(
            "INSERT INTO actions_log (ts, stage, entity_type, entity_id, action, detail_json) VALUES (?,?,?,?,?,?)",
            (_now(), stage, entity_type, entity_id, action, json.dumps(detail or {})))

    def start_run(self) -> int:
        return self.s.insert("INSERT INTO runs (started_at) VALUES (?)", (_now(),))

    def finish_run(self, run_id: int, stage_status: dict, summary: str):
        self.s.execute("UPDATE runs SET finished_at=?, stage_status_json=?, summary=? WHERE id=?",
                       (_now(), json.dumps(stage_status), summary, run_id))

    def close(self):
        self.s.close()
