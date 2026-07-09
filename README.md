# jobhunter

Autonomous job-search & outreach agent. See [BLUEPRINT.md](BLUEPRINT.md) for the
full architecture and [AGENT_SPEC.md](AGENT_SPEC.md) for the governing behavior spec.

## Status

- **Phase A — search + dedup + store: ✅ built & working** (key-free, no risk)
- **Phase B — profile modeling + match/rank + daily report: ✅ built & working**
- **Phase C — contact discovery + company research + email drafting + guarded sending: ✅ built**
  (runs in shadow mode until Azure/Apollo/Gmail creds are added; outreach pipeline verified via mock)
- **Scheduling — macOS launchd daily routine: ✅ ready to install** (see `deploy/`)
- Phase D — outcome tracking + learning: pending

The full daily pipeline is: **search → score → shortlist → research → find contact →
draft email → safety audit → validate → send/queue → report.** Every stage that needs a
credential degrades gracefully and reports what it skipped; nothing crashes, nothing sends
until you explicitly enable it.

## Setup

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

## Run the full daily routine

```bash
./.venv/bin/python -m jobhunter.cli routine    # search -> score -> shortlist -> report
./.venv/bin/python -m jobhunter.cli report      # print the latest report
```

Individual stages:

```bash
./.venv/bin/python -m jobhunter.cli search      # fetch all sources -> dedup -> store
./.venv/bin/python -m jobhunter.cli score       # score unscored jobs vs your profile
./.venv/bin/python -m jobhunter.cli list         # browse stored jobs (newest first)
./.venv/bin/python -m jobhunter.cli stats         # counts by source
```

Reports land in `data/reports/latest.md`. Data is in `data/jobhunter.db` (SQLite).
Re-running is idempotent — jobs dedup by company+title+location; scores upsert.

## Scoring: how it ranks (the north-star)

Ranked by **interview probability, not volume** (per AGENT_SPEC). Two stages:
1. **Heuristic (free, always on):** title-fit vs your `target_roles`, skill overlap vs
   `skills_core/secondary`, remote/location fit, recency (fresher = less competition = higher
   odds), with hard rejects for non-target roles, over-seniority, and over-experience JDs.
   A junior-seniority "stretch" penalty demotes senior/lead titles.
2. **Claude refinement (when `ANTHROPIC_API_KEY` set):** re-scores the top ~40 heuristic
   survivors with a calibrated interview probability + rationale, and can reject weak fits the
   heuristic missed. Set the model with `JOBHUNTER_MODEL` (default `claude-sonnet-5`).

Tune everything in `config/profile.yaml` (roles, skills, reject keywords, seniority ceiling).

## Schedule it (every morning)

See [deploy/README.md](deploy/README.md) — installs a launchd job that runs `routine` at 07:00
and fires a missed run on next wake.

## Configure your sources

Edit `config/sources.yaml` (copy from `config/sources.example.yaml`). Each entry is
one company's public ATS board — the token/slug from the ATS URL:

| ATS | URL pattern | `type` | `company` = |
|-----|-------------|--------|-------------|
| Greenhouse | `boards.greenhouse.io/<token>` | `greenhouse` | `<token>` |
| Lever | `jobs.lever.co/<slug>` | `lever` | `<slug>` |
| Ashby | `jobs.ashbyhq.com/<org>` | `ashby` | `<org>` |

Build your target list from YC companies, VC portfolios, and companies you want.
The example list (Anthropic, OpenAI, Databricks, Stripe, Mistral, Ramp) is just a
smoke test — replace it.

## To go live (turn shadow mode into real sending)

The code is all built. To activate each capability:

1. **LLM (scoring + email drafting)** — add Azure OpenAI vars to `.env`
   (`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`). See `.env.example`.
2. **Contact discovery** — put your Apollo API key(s) and/or a Hunter key in
   `secrets/apollo_accounts.yaml` (copy the example). Rotation + fallback waterfall is built.
3. **Gmail sending** — create an OAuth Desktop client, download it to
   `secrets/gmail_client_secret.json`, then run once:
   `./.venv/bin/python deploy/authorize_gmail.py`
4. **Flip autosend** — only after reviewing queued drafts (`cli queue`), set
   `autosend_enabled: true` in `config/outreach_rules.yaml`. Until then everything queues.

Review drafts any time with:

```bash
./.venv/bin/python -m jobhunter.cli queue
```

### Guardrails on live send (enforced in code, not by the model)
verified-email-only · daily cap (15) · per-company cap (1) · region exclusion (EU/UK/CA) ·
dedup · suppression/blocklist · job-still-live recheck · placeholder check · LLM safety audit
of every draft. Any failure → the email queues for review instead of sending.

## Directory map

```
jobhunter/
  cli.py            entry point (search / list / stats)
  ingest.py         orchestrates sources -> dedup -> store (fault-tolerant)
  db.py             SQLite schema + helpers
  models.py         JobPosting normalization + content-hash dedup
  config.py         YAML config + .env secrets loading
  sources/          ATS adapters (greenhouse, lever, ashby) + base HTTP/retry
config/             sources / profile / outreach-rules (+ .example templates)
secrets/            git-ignored credentials (Apollo pool, Hunter, etc.)
data/               SQLite DB (git-ignored)
```
