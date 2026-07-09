# Autonomous Job-Search & Outreach System — Technical Blueprint

**Status:** Design / pre-implementation
**Author:** Senior automation architecture review
**Date:** 2026-07-08

---

## 0. Read this first — the reality check

You asked me to challenge the design. Here are the five things that matter most, before any architecture:

1. **"Runs fully autonomously every morning and auto-sends cold emails" is the single riskiest decision in the whole plan.** It is where legal exposure, email-domain destruction, and embarrassing accuracy failures all concentrate. The correct v1 architecture is **draft-and-approve**: the routine does 95% of the work overnight (search, rank, research, find contacts, write emails) and leaves you a queue of ready-to-send drafts to approve in ~5 minutes each morning. You can graduate specific, proven paths to auto-send later. I'll architect for this and explain why throughout.

2. **"Rotate between multiple Apollo free accounts when quota runs out" is a Terms-of-Service violation that will not work reliably and can get all your accounts banned.** Apollo fingerprints devices/IPs and the OAuth connector cannot be re-authenticated headlessly in a scheduled run anyway. Drop this. Either pay for one Apollo tier that fits your real volume (you need ~5–15 lookups/day, not thousands) or use a legitimate multi-provider waterfall with paid API keys. I'll design the waterfall the right way.

3. **The Claude "Routine" (scheduled cloud agent) cannot, by itself, reliably do the two hardest parts: OAuth-authenticated MCP calls and browser automation.** The session tooling itself warns that interactively-authenticated MCP servers "may be absent in headless/cron runs." So Apollo-via-MCP and any Playwright scraping will not run in a bare cloud routine. The realistic architecture is **hybrid**: a small always-on worker service (your Mac, a $5–12/mo VPS, or a container) does the I/O with real API keys and stored sessions; Claude does the intelligence (matching, research synthesis, personalization) and orchestration. The "routine" is the daily trigger.

4. **LinkedIn automated scraping/login will get the account restricted or banned and violates their User Agreement.** Treat LinkedIn as a *read-by-hand / official-API-only* source, not an automation target. Use the legitimate ATS JSON endpoints (Greenhouse/Lever/Ashby) and job aggregators for volume instead — they're actually *easier* and legal.

5. **Cold email at volume from your personal domain will tank your deliverability and can suspend your Google account.** As a job seeker you want **low volume, high personalization** (5–20 emails/day), which is the safe zone — but even there you must comply with CAN-SPAM / GDPR / CASL and warm up sending. High-quality-low-volume is not a compromise here; it genuinely converts better than blasting.

None of this means the project is a bad idea. A draft-and-approve, hybrid system that hits the legitimate ATS APIs, uses one paid contact-data provider with a clean fallback, and writes genuinely personalized emails is highly valuable and very buildable. That's what this blueprint specifies.

---

## 1. Feasibility matrix — every step, honestly classified

| Workflow step | Fully auto? | Mechanism | Reality / caveat |
|---|---|---|---|
| Parse resume/portfolio/LinkedIn into a profile | ✅ Yes | LLM + embeddings, one-time + on change | You provide the docs. Trivial and high-value. |
| Semantic profile model (strengths, ATS keywords, fit) | ✅ Yes | Claude + embeddings | Best-in-class use of the LLM. |
| Search Greenhouse / Lever / Ashby | ✅ Yes | **Public JSON APIs** | Legal, stable, no auth. The backbone of your search. |
| Search Workday | ⚠️ Partial | Per-tenant JSON (`/cxs` endpoints) | Works but brittle per company; needs a tenant list. |
| Search Google Jobs / aggregators | ⚠️ Paid | SerpAPI / JSearch (RapidAPI) | No free official API; budget ~$50/mo. |
| Search LinkedIn Jobs | ❌ Avoid automating | Manual or paid data vendor | ToS + anti-bot. Do not script logins. |
| Search Wellfound / YC / accelerators | ⚠️ Partial | Some have feeds; else light scraping of public pages | YC has a public jobs page/API-ish; Wellfound is bot-hostile. |
| Rank & score jobs vs profile | ✅ Yes | Embeddings + LLM rubric | Core intelligence. Fully automatable. |
| Company research | ✅ Yes | Web search + LLM synthesis | Cite sources; cache to avoid re-fetching. |
| Contact discovery (who to email) | ⚠️ Paid API | Apollo/Hunter/RocketReach **API keys** | Not free at volume. One paid provider + fallback. |
| Email verification | ✅ Yes | Provider's verification + syntax/MX/SMTP check | Never send to "guessed" addresses. |
| Personalized email drafting | ✅ Yes | Claude with your style guide + context | The LLM's strongest job here. |
| **Sending email** | ⚠️ **Gate this** | Gmail/Workspace API or ESP | **Human-approve in v1.** Compliance + deliverability. |
| Dedup / expiry / state | ✅ Yes | Database + idempotency keys | Straightforward engineering. |
| Reporting | ✅ Yes | Query DB → Claude summary → email/Slack | Easy. |
| Continuous learning / optimization | ⚠️ Later | Track outcomes, adjust weights | Real, but needs weeks of data first. Start by *logging*, optimize in v2. |
| CAPTCHA / anti-bot handling | ❌ Don't | — | If you hit a CAPTCHA, you're on a source you shouldn't be scraping. Reroute to an API. |

**Rule of thumb:** anything that reads *public structured data* or *reasons over text* is fully automatable. Anything that requires *logging into a human's account* or *sending as you* needs either a paid API or a human gate.

---

## 2. What you must provide (inputs & configuration)

### 2.1 Profile data (you supply once, update as needed)
- Resume (PDF + the source .docx/.md if you have it — better parsing).
- Portfolio URL and/or project write-ups.
- LinkedIn profile export (Settings → Get a copy of your data) — **use the export, don't scrape**.
- A `profile.yaml` I'll help you generate: preferred roles, seniority, locations, remote/hybrid/onsite, salary floor/target, visa/work-authorization status, industries you want and want to avoid, company-size preference, dealbreakers.
- **Extra context that materially improves matching/personalization** (this is where most people under-invest):
  - "Brag doc" — quantified wins, metrics, shipped projects.
  - Your genuine interests / what excites you (for authentic personalization).
  - Constraints: earliest start date, timezone, relocation willingness.
  - Any warm connections / companies where you know someone.
  - Roles/companies you've already applied to (to seed the dedup DB).

### 2.2 Credentials & keys (stored as secrets, never in code)
- **Anthropic API key** (for the intelligence layer if running outside the Claude harness).
- **Contact data:** one paid provider to start — Apollo API key *or* Hunter API key. (Recommend Hunter for simple email-finding + verification; Apollo if you also want people-search/filtering. Not both to begin.)
- **Job search enrichment:** SerpAPI or RapidAPI (JSearch) key — optional but recommended.
- **Email sending:** Google Workspace account with API access (OAuth client) *or* a transactional/cold-email ESP. See §7.
- **Reporting sink:** a Slack webhook or just your own email.

### 2.3 Templates & rules (you supply)
- 2–4 email templates (recruiter, hiring manager, founder, referral-ask) as *skeletons with intent*, not fill-in-the-blank.
- `style_guide.md`: tone, do/don't phrases, banned clichés ("I'm reaching out…", "I hope this finds you well"), signature, length target.
- `outreach_rules.yaml`: max emails/day, quiet hours, per-company cap, follow-up cadence, blocklist (companies/domains to never contact), regions to exclude for compliance.

---

## 3. Recommended architecture (challenging your design)

### 3.1 The core change: decoupled pipeline + human gate

```
                    ┌─────────────────────────────────────────────────┐
   DAILY TRIGGER    │  Claude Routine (cloud cron)  — orchestrator      │
   (cron 6:00am) ──▶│  OR local cron on your Mac/VPS                     │
                    └───────────────┬─────────────────────────────────┘
                                    │ invokes
                                    ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │                      WORKER SERVICE (always-on: Mac / VPS / container)  │
   │                                                                        │
   │  [1] Profile Service ──▶ profile model + embeddings (cached)           │
   │            │                                                           │
   │  [2] Job Ingest ──▶ Greenhouse/Lever/Ashby/Workday/SerpAPI adapters    │
   │            │         (normalize → JobPosting; dedup by content hash)   │
   │            ▼                                                           │
   │  [3] Match & Rank ──▶ embeddings + Claude rubric → confidence score    │
   │            │         (reject < threshold automatically)                │
   │            ▼                                                           │
   │  [4] Company Research ──▶ web search + Claude synthesis (cached 30d)    │
   │            ▼                                                           │
   │  [5] Contact Discovery ──▶ provider waterfall (Apollo→Hunter→…)        │
   │            │              → verify email → store w/ confidence         │
   │            ▼                                                           │
   │  [6] Personalize ──▶ Claude drafts email per contact (style guide)     │
   │            ▼                                                           │
   │  [7] Pre-send checks ──▶ dedup, job-still-live, links OK, grammar      │
   │            ▼                                                           │
   │      ┌─────────────────────────────┐                                   │
   │      │  APPROVAL QUEUE (v1)         │◀── you review each morning (5m)   │
   │      │  auto-send allowlist (v2)    │                                   │
   │      └──────────────┬──────────────┘                                   │
   │                     ▼                                                   │
   │  [8] Send ──▶ Gmail/Workspace/ESP  (rate-limited, warmed-up)           │
   │                     ▼                                                   │
   │  [9] Track ──▶ SQLite/Postgres (every action logged, idempotent)       │
   │                     ▼                                                   │
   │  [10] Report ──▶ Claude summary → email/Slack                          │
   └──────────────────────────────────────────────────────────────────────┘
```

**Why decoupled stages, not one big script:** each stage is independently retryable, cacheable, and testable. A failure in contact discovery must not lose the ranked jobs you already computed. Each stage reads from and writes to the DB, so a crash resumes from the last completed stage (idempotency via content-hash keys).

**Why the worker service instead of doing it all inside the routine:** the cloud routine runtime can't hold OAuth sessions or run a browser reliably, and you don't want your API keys living in a hosted agent context. Keep secrets and stateful I/O in your own always-on process; let Claude be the brain and the scheduler.

### 3.2 Where Claude fits (and where it shouldn't)
- **Claude does:** profile modeling, job-fit scoring rubric, company research synthesis, email drafting, daily report writing, deciding *which* contact to target. High-judgment, text-heavy tasks.
- **Deterministic code does:** API calls, dedup, rate limiting, retries, scheduling, DB writes, email verification, provider rotation. Anything that must be exact and cheap. Don't burn tokens on plumbing.

---

## 4. Phase-by-phase deep analysis

### Phase 1 — Profile understanding
- Parse docs → structured `Profile`. Compute **embeddings** for each skill/project/experience bullet (Claude/OpenAI/`text-embedding` model). Store vectors (SQLite + `sqlite-vec`, or pgvector).
- Claude produces a `ProfileInsight`: strongest skills, gaps, transferable skills, ATS keyword set, best-fit industries, "avoid" list. Regenerate only when you edit inputs (cheap, cached).
- **Edge case:** resume PDF with columns/tables parses badly → keep a plain-text master copy as source of truth.

### Phase 2 — Job search
- **Primary sources (build adapters):**
  - Greenhouse: `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
  - Lever: `https://api.lever.co/v0/postings/{company}?mode=json`
  - Ashby: `https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true`
  - Workday: per-tenant `.../wday/cxs/{tenant}/{site}/jobs` POST endpoints (brittle; maintain a tenant map).
  - Aggregator: SerpAPI Google Jobs *or* JSearch (RapidAPI) for breadth.
- **You need a company/board seed list.** The dirty secret of "search everywhere": you must know *which* Greenhouse/Lever tokens to hit. Build a seed list from YC companies, accelerator portfolios, and your target list; grow it over time. I'll scaffold a `sources.yaml`.
- **"Search until diminishing returns":** implement as *breadth budget* — N sources × M pages, stop when new-unique-jobs-per-source drops below a threshold. Don't loop forever; log what was skipped.
- Recency: capture `posted_at`; bucket into today / 24h / 3d / 7d; older only if score is exceptional.
- **Don't** script LinkedIn/Indeed/Wellfound logins. If you want their listings, do it via a paid data vendor with ToS coverage, or manually.

### Phase 3 — Match & rank
- Two-stage: (a) cheap embedding similarity to pre-filter, (b) Claude scores survivors against a rubric returning a structured score + reasons + reject flag.
- **Score dimensions** (weighted, weights tunable later from outcome data): skills fit, seniority fit, ATS keyword overlap, comp fit, remote fit, company quality/stage, hiring urgency signals, estimated competition. Output a 0–100 confidence + a one-line rationale + explicit reject reason when rejected (needed for your report).
- **Edge case:** LLM score drift → pin the rubric, use structured output, spot-check weekly.

### Phase 4 — Company research
- Web search + synthesis: products, tech stack, funding, recent news, AI initiatives, growth signals. **Cache per company for ~30 days.** Only gather what improves outreach — enforce that in the prompt to control cost.

### Phase 5 — Contact discovery (the part you most over-engineered)
- **Target selection first, lookup second.** Decide the ideal contact (recruiter for big cos, hiring/eng manager mid, founder/CTO early-stage) from the job + company stage, *then* look them up. One good contact beats five wrong ones.
- **Provider waterfall (the legitimate version):**
  1. Apollo **API** (paid seat/credits) — people search + email.
  2. Hunter API — domain search + email finder + **verification**.
  3. RocketReach / ContactOut API — fallback.
  4. Company website / public "team" page (light, respectful fetch).
  Switch providers on `quota_exhausted` or `not_found`, driven by a config list of `{provider, api_key, monthly_cap}`. **Never rotate free accounts to dodge quotas.**
- **Verification is mandatory:** syntax → MX record → provider's verification score → SMTP probe (careful, some servers blocklist probers). Store `verification_status ∈ {verified, risky, unknown}`. **Never send to `risky`/`unknown` in v1.** Never fabricate/pattern-guess and send.
- Store per contact: full name, title, company, LinkedIn URL, email, source provider, confidence, verification status, retrieved_at.

### Phase 6 — Personalization
- Feed Claude: job description + company research + contact role + your matching projects + your style guide. Prompt for a *specific* hook (a real detail about the company/role) and forbid generic openers. Generate 1 draft + a 1-line "why this hook" note so you can sanity-check.
- **Anti-"AI smell":** ban listed clichés, cap length, require one concrete specific, vary structure across emails, keep your real voice from the style guide. Route a sample through your own read before trusting.

### Phase 7 — Sending (gate hard)
- **v1: draft → approval queue → you click send.** Approval can be a simple web page, a Slack message with buttons, or replying to the report email.
- Pre-send checklist (automated): job still live (re-hit the posting URL), not previously contacted (dedup key = `contact_email + company + role`), links resolve (HTTP 200), correct resume version attached, grammar pass, personalization sanity check.
- Sending mechanics: Gmail/Workspace API. Respect limits (**consumer Gmail ~500/day; Workspace ~2,000/day external**, but *cold* email should stay **≤20–40/day** to protect reputation). Warm up over weeks. Add unsubscribe/opt-out line, your real name, and a physical mailing address (CAN-SPAM). Randomized send spacing, quiet hours, per-domain cap.
- **v2 auto-send graduation:** only for (contact verified) AND (score ≥ high threshold) AND (template class you've approved ≥N times) AND (within daily cap). Everything else stays gated.

### Phase 8 — Storage
See schema in §9. Every action is a row; every external object has an idempotency key; nothing is deleted (soft-delete + status).

### Phase 9 — Reporting
Query the DB, hand Claude the day's numbers, produce a tight report: searched/shortlisted/rejected (+top reject reasons), companies researched, contacts found, drafts ready, sent, failures, remaining quotas, recommended follow-ups, and 2–3 concrete "improve your search" suggestions. Deliver via email/Slack.

### Continuous learning
- **v1 = instrument, don't optimize.** Log outcomes: opens (if you use tracking — note privacy tradeoffs), replies, interviews, rejections, per source/template/company. After ~3–4 weeks you'll have signal.
- **v2 = adjust:** reweight scoring dimensions toward sources/companies that yield replies; A/B email openers; suppress companies that repeatedly reject. Keep it explainable (adjust weights), not a black-box model — you need to trust it.

---

## 5. Legal, ToS & deliverability (do not skip)

- **CAN-SPAM (US):** applies to commercial email; job-seeking outreach is borderline but comply anyway — accurate headers, no deceptive subject, physical address, honor opt-outs.
- **GDPR / ePrivacy (EU/UK) & CASL (Canada):** contacting EU/UK/Canada individuals raises the bar. B2B cold email can rely on "legitimate interest" in some EU states but you must offer opt-out, keep records, and honor deletion. **Recommendation:** region-gate — exclude EU/UK/Canada contacts in v1 unless you're prepared to comply, configurable in `outreach_rules.yaml`.
- **Apollo/Hunter/etc. ToS:** using their *APIs within your plan* = fine. Rotating free accounts to evade quotas = violation → bans. Don't.
- **LinkedIn User Agreement:** prohibits automated scraping/bot access. Manual use only; official Talent APIs require partnership. Don't script it.
- **Email deliverability is a reputation system, not a rule you can ignore:** SPF, DKIM, DMARC on your domain; warm-up; low volume; high engagement (replies help, bounces/spam-marks hurt). One bad blast can burn the domain. Consider a *separate* domain if you ever go higher-volume, but for personalized job outreach your real domain at low volume is best (recruiters trust it).

---

## 6. Tech stack (recommended, pragmatic)

- **Language:** Python 3.12 (best ecosystem for scraping/APIs/data).
- **Orchestration in-process:** a simple stage runner (or Prefect/Dagster if you want observability; start simple).
- **HTTP:** `httpx` with retry/backoff (`tenacity`).
- **Browser (only if unavoidable, for public pages):** Playwright, headful-ish, respectful rate limits. Prefer APIs first.
- **LLM:** Claude via Anthropic API (`claude-fable-5` for judgment, a cheaper tier for bulk classification). Embeddings via a dedicated embedding model.
- **DB:** **SQLite + `sqlite-vec`** for single-user v1 (zero-ops, file-backed). Migrate to **Postgres + pgvector** if you scale/multi-user.
- **Secrets:** `.env` + OS keychain, or a secrets manager; never in git.
- **Email:** Google Workspace API (OAuth) for v1; Instantly/Smartlead only if you later go higher volume.
- **Scheduling:** local `cron`/launchd on your Mac, or a cheap VPS; the Claude Routine is the "brain trigger" that calls the worker.
- **Reporting:** Slack incoming webhook or SMTP to yourself.

---

## 7. Email architecture specifics
- **v1:** Google Workspace + Gmail API, OAuth desktop flow, token refresh stored securely. Send as you, plain-looking, personalized, ≤20–40/day, spaced out.
- **Auth:** OAuth 2.0 with offline refresh token; re-consent handled by the worker, not the cloud routine.
- **Bounce/complaint handling:** poll for bounces (NDRs) and auto-mark emails invalid; suppress that address forever.
- **Threading & follow-ups:** store `Message-ID`; follow-ups reply in-thread; cadence e.g. +3 business days, max 1–2 follow-ups, stop on reply.

---

## 8. Reliability & error handling

| Failure | Handling |
|---|---|
| API/network error | Exponential backoff + jitter (`tenacity`), max N retries, then mark stage `deferred` and continue. |
| Provider quota hit | Waterfall to next provider; log remaining quotas. |
| Site layout change (scraper) | Adapter has a self-test/canary; on parse-fail, alert + skip source, don't crash pipeline. |
| CAPTCHA | Treat as "stop, this source is off-limits" — reroute to API. Do **not** solve/bypass. |
| Crash mid-run | Stage checkpoints in DB + idempotency keys → resume, no dupes. |
| Expired job | Re-validate URL at pre-send; mark `expired`, skip. |
| Duplicate job/contact/email | Content-hash + unique constraints; dedup before any spend or send. |
| Invalid/risky email | Never send; mark, try next contact or skip. |
| Email delivery failure | Retry once; on bounce, suppress address. |
| Partial failure | Per-stage status so the report shows exactly what completed. |

---

## 9. Database schema (v1, SQLite; portable to Postgres)

```sql
profiles(id, data_json, embedding, updated_at)
sources(id, type, token, company, enabled)                 -- greenhouse/lever/ashby/...
jobs(id, source_id, external_id, content_hash UNIQUE, title, company, location,
     remote, posted_at, url, description, raw_json, first_seen, status)  -- status: new/scored/rejected/shortlisted/expired
job_scores(job_id, score, dimensions_json, rationale, reject_reason, model, scored_at)
companies(id, name, domain, research_json, funding, stage, researched_at)
contacts(id, company_id, full_name, title, linkedin_url, email,
         source_provider, confidence, verification_status, retrieved_at,
         UNIQUE(email, company_id))
emails(id, job_id, contact_id, template_class, subject, body, hook_note,
       status, approved_by, message_id, sent_at, error,
       UNIQUE(contact_id, job_id))                          -- status: draft/approved/sent/bounced/replied/suppressed
followups(id, email_id, due_at, sent_at, status)
replies(id, email_id, received_at, sentiment, snippet)
provider_usage(provider, period, used, remaining, updated_at)
actions_log(id, ts, stage, entity_type, entity_id, action, detail_json)  -- append-only audit
runs(id, started_at, finished_at, stage_status_json, summary)
suppression(email UNIQUE, reason, added_at)                 -- opt-outs, bounces, blocklist
```

---

## 10. Cost estimate (rough, monthly)

| Item | Est. cost |
|---|---|
| Anthropic API (matching + research + drafting, ~20–40 jobs/day) | $30–120 |
| Contact data (Apollo starter or Hunter) | $30–99 |
| Job aggregator (SerpAPI/JSearch) — optional | $0–75 |
| Google Workspace (if not already) | $6–12 |
| VPS (if not running on your Mac) | $5–12 |
| **Total** | **~$70–320/mo**, tunable down by lowering daily volume and caching aggressively. |

The big lever is *volume*. 10 excellent personalized emails/day costs little and converts better than 200 blasts.

---

## 11. Scalability, bottlenecks, maintenance
- **Bottlenecks:** contact-data quotas (money), LLM cost (control volume + cache), scraper fragility (prefer APIs), email reputation (low volume).
- **Scale path:** SQLite→Postgres, add a queue (Redis/RQ) if stages parallelize, containerize the worker.
- **Maintenance:** ATS adapters need occasional fixes; keep them isolated with canary self-tests. Refresh the source/company seed list monthly. Rotate keys. Review the auto-send allowlist as data accrues.

---

## 12. Improvements beyond your original design
1. **Draft-and-approve default** (biggest risk reduction, better quality).
2. **ATS-API-first search** instead of scraping LinkedIn — legal, stable, and honestly better coverage of startups.
3. **Referral-path detection:** before cold emailing, check if you have a 1st/2nd-degree connection (from your LinkedIn export) — a warm intro beats any cold email. Add a "referral opportunity" flag to the report.
4. **Tailored resume/cover suggestions per job** (ATS keyword gaps) — high ROI, the LLM is great at it.
5. **Instrument-first learning loop** — log everything now, optimize weights once you have data; keep it explainable.
6. **Region compliance gating** built into config from day one.
7. **A "why rejected" trail** so you can trust and tune the ranker.

---

## 13. Recommended build order (phased)
- **Phase A (week 1):** Profile service + Greenhouse/Lever/Ashby adapters + DB + dedup. Output: a daily ranked-jobs report, no outreach. Proves the search/rank core cheaply.
- **Phase B (week 2):** Company research + contact discovery (one paid provider) + email drafting → **approval queue**. You send approved emails yourself.
- **Phase C (week 3):** Gmail API sending from the queue + follow-ups + bounce handling + full reporting.
- **Phase D (week 4+):** Add aggregator/Workday sources, outcome tracking, and *selective* auto-send graduation.

Ship A end-to-end before building B. Each phase is independently useful.

---

## 14. Decisions made (2026-07-08) + consequences

| Decision | Your choice | Consequence / mitigation |
|---|---|---|
| Send mode | **Full auto-send from day 1** | Highest-risk path (account suspension, domain reputation, unreviewed content going out as you, legal exposure). **Non-negotiable guardrails in §15** make this survivable. Strongly recommend a 1–2 week "shadow" period where it drafts but you eyeball the log before flipping the send switch. |
| Worker host | **Your Mac** | Runs only when awake + online. Use `launchd` (not cron) so a missed run fires on wake. Laptop asleep at 6am = no run that day. |
| Contact provider | **Apollo, free tier only** | ⚠️ **Likely a hard blocker for daily automation** — see §16. Apollo's free tier gives a small monthly credit allotment and restricted API access; automated daily lookups will exhaust it within days and some API endpoints aren't available on free. We build the provider behind an interface so you can add a paid key or a Hunter free key later without rewiring. |

### Still needed from you
4. **Region policy** — exclude EU/UK/Canada contacts in v1? (Recommended yes, for GDPR/CASL.)
5. **Daily send cap** — recommend 10–20 to protect deliverability even in auto-send.
6. **Email identity** — which Google account/domain sends? Is SPF/DKIM/DMARC configured? (Required before any auto-send.)

---

## 15. Non-negotiable auto-send guardrails (since you chose full auto)

Auto-send only proceeds for an email that passes **every** gate; otherwise it drops to a review queue instead of sending:

1. **Verified email only** — `verification_status == verified`. Never `risky`/`unknown`, never pattern-guessed.
2. **High-confidence job match only** — score ≥ a high threshold (e.g. 80/100). Marginal matches queue for review.
3. **Hard daily cap** — default 15/day, randomized spacing, quiet-hours respected. Cap is enforced in code, not by the LLM.
4. **Dedup + suppression** — never a second email to the same `contact+company+role`; honor blocklist and opt-outs absolutely.
5. **Content safety pass** — a second Claude call reviews each drafted email for hallucinated facts, wrong company/name, broken links, missing attachment, and "AI smell" before it can send. Fail → queue, don't send.
6. **Compliance footer** — real name, opt-out line, physical mailing address (CAN-SPAM).
7. **Live-job recheck** — re-hit the posting URL at send time; expired → skip.
8. **Kill switch** — a single config flag (`AUTOSEND_ENABLED=false`) and a daily send-budget that, if exceeded, halts and alerts.
9. **Bounce circuit-breaker** — if bounce rate in a rolling window exceeds ~5%, auto-pause sending and alert (protects your domain).
10. **Shadow mode first** — ship with `AUTOSEND_ENABLED=false` so the first days produce a full "would have sent" log for you to audit; flip to true once you trust it.

---

## 17. Reconciliation with AGENT_SPEC (the master prompt)

The master prompt (`AGENT_SPEC.md`) is honored in **intent**. Three items need a mechanism swap because the literal instruction is a ban-risk, illegal, or technically impossible in a scheduled run. The *goal* behind each is preserved.

| Spec item | Conflict | Resolution (preserves the goal) |
|---|---|---|
| **Stage 15 — Apollo multi-account rotation** ("you will receive multiple Apollo accounts, log out, switch, continue") | Rotating free accounts to evade quotas **violates Apollo ToS** (ban risk to all accounts) and detection is likely. | **User decision (2026-07-08): keep rotation.** Implemented per §18 with eyes open. Rotation is the primary Apollo path; the legal waterfall (§16) stays wired in as the automatic safety net for when rotation fails or is blocked. |
| **Stages 3 & 10 — "search the entire internet": LinkedIn, Indeed, Glassdoor, Naukri, Foundit, Instahyre, Twitter/X, Reddit, Discord, Slack scraping** | Most of these **prohibit automated scraping/bot login** (LinkedIn/Indeed/Glassdoor especially) and are anti-bot hardened; scripting them risks account bans and legal exposure, and CAPTCHAs will block a headless run. | Goal = *maximum coverage of real, low-competition openings.* Achieved via **legal high-yield sources**: ATS JSON APIs (Greenhouse/Lever/Ashby), Workday tenants, YC/Wellfound public data, **HN "Who's Hiring" (public, scriptable)**, RemoteOK/WWR feeds, aggregator API (SerpAPI/JSearch) for Google-Jobs breadth, and RSS/newsletters. LinkedIn/Twitter/Discord are treated as **manual or read-only** leads surfaced in the report, not scraped. This actually covers startups *better* than LinkedIn. |
| **Stage 19 — auto-send via Gmail, "every morning, never skip"** | Consistent with your locked choice, but unattended sending is where account-suspension and reputation risk concentrate. | Kept, wrapped in the **§15 guardrails** (verified-only, score≥threshold, hard cap, content-safety pass, bounce breaker, kill switch) and shipped in **shadow mode** first. |

Everything else in the spec (the north-star metric, truth constraints, stages 1–2, 4–14, 16–18, 20–21, resume/interview scoring, hidden-job discovery *via legal sources*, crowd/competition analysis, company research, personalization, validation, DB, reporting) is adopted directly and becomes the ranking/behavior contract for the build.

**One genuinely load-bearing point:** the north-star — *interview probability, not volume* — is excellent and I'm making it the literal objective of the ranker (Stage 9), which is what turns this from a spam machine into a targeting system. Good instinct.

---

## 18. Apollo multi-account rotation — honest implementation design

You chose to keep rotation. The *only* thing that determines whether it works is **which access path each free account gives you**, and there are two very different cases:

### Case A — each free account exposes a REST **API key** (the good case)
If Apollo's free tier gives you an API key, rotation is clean and headless-friendly:
- Store a pool: `[{account_label, api_key, monthly_cap, used, status}]` in the DB (`provider_usage`), keys in the secret store — **never in git**.
- Call the Apollo REST API with the current key. On `429`/quota-exceeded/credit-error, mark that key `exhausted`, advance to the next key, and **continue from the same work item** (idempotency key = the contact lookup), so no re-search and no lost progress — satisfying Stage 15's "continue exactly where it stopped."
- Reset `used` counters monthly. Log remaining quota per account for the daily report.
- No browser, no OAuth, no headless-login problem. This is just key-swapping on error. **This is the design I'll build for.**

### Case B — free tier is **OAuth/MCP only, no API key** (the likely case)
Apollo has historically gated REST API access behind paid plans. If your free accounts have *no* API key, "rotation" means driving the web UI with browser automation and swapping logged-in sessions. Honest constraints you're accepting:
- Each account needs a **pre-captured browser session** (cookies/localStorage) saved once by you interactively; the worker reuses them. Sessions expire → periodic manual re-login. A 6am unattended run **cannot** perform a fresh OAuth/password login on its own.
- Logging in/out repeatedly from one machine/IP is exactly the fingerprint Apollo's bot-detection watches for → elevated ban probability for the whole pool.
- Expect breakage on UI changes and occasional CAPTCHAs (which halt a headless run).
- Mitigations I'll add: reuse a stored session per account (don't re-login each run), randomized human-like pacing, one account per run-day when possible, and **immediate fallback to the §16 waterfall** the moment a session is dead or a CAPTCHA appears — so a broken rotation degrades to still-working free discovery instead of crashing the pipeline.

### What I'll build (works in both cases)
A `ContactProvider` interface with an `ApolloRotatingProvider` that:
1. tries API-key rotation (Case A) if keys are present;
2. else uses stored-session rotation (Case B);
3. on total Apollo failure, hands off to the waterfall (company team-page parse → Hunter free verify);
4. records per-account usage/quota, and reports "Apollo: account 2/4 active, 3 exhausted, N via fallback."

You supply the account credentials/keys via a `secrets/apollo_accounts.yaml` (git-ignored). **To be clear on residual risk:** this can get those accounts banned and won't be as reliable as a single paid key — but it's your call and it's built to fail *safe*, not to crash.

---

## 16. The Apollo-free-tier problem (read before building Phase B)

You asked for **Apollo free tier only**. Concretely, for a *daily automated* job:
- Apollo's free plan provides only a **small monthly credit allotment** (email/export credits) and **restricted API access** — several people/email endpoints require a paid plan, and limits change frequently. Verify current limits in your Apollo account before relying on them.
- The MCP connector in this session is **OAuth-interactive**, and the harness warns such connectors "may be absent in headless/cron runs" — so the Apollo *MCP* likely won't function inside a scheduled routine at all. Automated use needs the **Apollo REST API with a key**, whose availability depends on your plan tier.
- **Net:** daily automation on Apollo free will run dry within days, and may not authenticate headlessly. This isn't a risk to manage — it's a ceiling.

**How we handle it without you paying yet:**
- Build contact discovery behind a `ContactProvider` interface. Ship an `ApolloProvider` (uses whatever your free tier allows) **plus a free-of-charge fallback chain**: company website/team-page parsing + a Hunter *free* key (25–50 verifications/mo) for verification. When Apollo credits hit zero, the waterfall continues on the free fallbacks and the daily report shows "Apollo quota exhausted — N contacts via fallback, M skipped."
- This keeps you at $0 for contacts, degrades gracefully instead of crashing, and lets you drop in a paid Apollo/Hunter key later by changing one config line — no rewrite.
- **Reality to accept:** on free tiers, some days will find fewer verified contacts, and the auto-send guardrail (#1, verified-only) means those simply won't send. That's the correct behavior — better to send nothing than to send to a guessed address.
