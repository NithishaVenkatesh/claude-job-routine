# Cloud Routine Configuration — Daily Job Search & Outreach

Snapshot of the live claude.ai cloud routine as of 2026-07-10.
Manage it at https://claude.ai/code/routines (or ask Claude Code).

| Setting | Value |
|---|---|
| Routine ID | `trig_015PwiWY8AJRpuWEtdmSovRr` |
| Status | Enabled |
| Schedule | `45 0 * * *` UTC = 6:15 AM IST daily |
| Model | claude-sonnet-5 |
| Repo cloned in sandbox | https://github.com/NithishaVenkatesh/claude-job-routine |
| Prompt | See [PROMPT.md](PROMPT.md) (exact copy) |
| Allowed tools | Bash, Read, Write, Glob, Grep, WebSearch, WebFetch |

## Connectors attached
- **Vibe_Prospecting** — contact finding (primary, working)
- **Apollo-io** — contact finding fallback (connector stale/disconnected on claude.ai)
- **Google-Drive** — run-to-run memory via `jobhunter_state.json` (NEEDS RE-AUTH at claude.ai/settings/connectors; until then runs have no memory and leads repeat)
- **Composio** — Gmail draft creation in nithisha.codes@gmail.com (connected 2026-07-10, account `ca_bGbkeRE5haE8`, alias "job-outreach")

## Safety design
- Outreach emails are created as **Gmail drafts only** — the prompt forbids every send tool. Nithisha reviews in Gmail → Drafts and sends manually.
- Search is exhaustive by design: no caps on searches or lead counts; stops only when 2 consecutive rounds yield <2 new leads and all six source families are covered. 1–2 h runtime expected.

## Job criteria (user-confirmed 2026-07-10)
- Junior AI/ML/LLM/agentic/RAG/Python-backend roles
- ₹4–12 LPA full-time; internship/contract/part-time also welcome (any reasonable pay)
- Location: hybrid Coimbatore/Bangalore preferred → remote anywhere (India or global) → on-site Coimbatore/Bangalore. Other-city on-site rejected.

## How to change the routine
The live prompt lives in the trigger config on claude.ai, NOT in this repo.
Ask Claude Code: "update the job routine to ..." — it uses the RemoteTrigger API,
then re-syncs PROMPT.md here. There is also a second parked routine
"egress-diagnostic (temp)" (disabled one-shot network test) that can be deleted.
