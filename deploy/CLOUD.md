# Running as a Claude Code cloud routine

The system is portable: with `DATABASE_URL` set it uses **Neon Postgres** (persistent,
survives between cloud runs); without it, local SQLite. That's what makes a stateless
cloud routine viable — dedup and tracking live in Neon, not in the ephemeral sandbox.

## What the routine does each run
Clones this repo → installs deps → runs `python -m jobhunter.cli routine` against Neon.
That executes: search → score → shortlist → research → contact → draft → validate →
send/queue → report. Missing credentials degrade gracefully (shadow mode).

## Secrets the routine needs (set as environment variables in the routine)
| Var | Purpose | Needed for |
|-----|---------|-----------|
| `DATABASE_URL` | Neon Postgres connection string | **required** (persistent state) |
| `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_DEPLOYMENT` | LLM | scoring refinement + email drafting |
| `APOLLO_API_KEYS` | comma-separated Apollo keys | contact discovery |
| `HUNTER_API_KEY` | Hunter fallback | contact discovery fallback |

⚠ Secrets placed in a routine are stored server-side. Use least-privilege, scoped keys,
and rotate the Neon password (the one shared in chat is already exposed — rotate it).

## Gmail sending in the cloud
Gmail OAuth is the hardest piece to move to the cloud (the token is a local file). For v1,
keep `autosend_enabled: false` — the cloud routine will still find jobs, research, discover
contacts, and **draft** emails into the Neon `emails` table (status `queued`). You review
them (query Neon or run the report), and send from your Mac, until Gmail-in-cloud is set up.

## The routine prompt (self-contained — the cloud agent starts with zero context)
```
You are running a scheduled daily job-search routine from this repository.
Steps:
1. Ensure Python 3 and pip are available.
2. Install dependencies:  pip install -r requirements.txt
3. Run the daily pipeline:  python -m jobhunter.cli routine
4. Print the full report (data/reports/latest.md) to output so it appears in the run log.
The DATABASE_URL and any provider keys are provided as environment variables. Do not commit
anything. Do not modify code. If a stage reports missing credentials, that is expected —
report what ran and what was skipped.
```

## First run is slow, later runs are fast
The initial ingest writes every current posting to Neon. After that, only genuinely new
postings insert (dedup by content hash), so subsequent mornings are quick.
```
