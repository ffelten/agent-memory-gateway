# Handoff — Florian's agent (Agents A/B, gateway client, demo)

Fallback channel until we have the shared GitHub repo + gh access. Florian: paste the "Integration proposal" section into the GitHub issue **"Agent integration coordination"**.

## Status
- **Completed:** repo lane scaffolding (`agents/ demo/ contracts/ tests/`); synthetic CRM fixture (`demo/crm_fixture.py`, stdlib, tested); shared fixture manifest (`contracts/fixtures.json`).
- **Branch/PR:** not yet — need the repo URL to push a `florian/agents-demo` branch.
- **Checks run:** CRM fixture — Alpha bearer → 200 + record; Beta bearer → 403; no bearer → 401. Backs acceptance check "B's Beta credential fails on Alpha; A's succeeds."
- **Interface changes:** none to shared contract. Proposing `contracts/fixtures.json` as the shared fixture manifest (below).
- **Blockers / need from Florian:** (1) shared GitHub repo URL + confirm `gh` push access; (2) **AkashML key** to run Agents A/B; (3) who owns **ClickHouse telemetry** (brief says it's separately assigned — I will not start it until confirmed); `build/clickhouse.sql` is a draft to hand that owner.
- **Need from Ash + Codex:** confirm the five interface points below.

---

## Integration proposal (post to the coordination issue)

**My scope (Florian + agent):** Agents A/B on AkashML, the gateway client, the demo runner (protected vs unprotected), the Python bypass attempt, and the demo output page. I build against a clearly-labeled **mock gateway** that implements your contract, and will not report mock runs as real integration.

**Gateway interface I consume** (from the spec contract — please confirm or correct):

1. **Auth & identity.** Agents send `Authorization: Bearer <run-scoped opaque token>`; the gateway derives tenant/principal/run server-side. The agent runtime holds only the run token — no Senso/AWS keys. The trusted harness (not the agent) mints runs via `POST /v1/admin/runs {template_id, principal_id}` → `{run_id, token, source_ids}`.
   - *Need:* how does the demo harness get the admin credential, and what env var should the agent read its run token from?

2. **Memory write.** `POST /v1/memories {title, text}` + `Idempotency-Key` → `202 {memory_id, state, reason_codes[], trace_id}`, no echo of sensitive text. States: `PENDING→QUARANTINED | INGESTING→APPROVED`.
   - *Need:* the `reason_codes` enum (e.g. `SECRET_MATCH`, `PROVENANCE_UNKNOWN`, `SCANNER_ERROR`, `TIMEOUT`).

3. **Memory read.** `POST /v1/memories/search {query, max_results<=5}` → `{results:[{memory_id, text, source_urls, application_version}]}` (allowed passages only). `GET /v1/memories/{id}` → authorized content or **404** (never 403, to avoid revealing existence). `GET /v1/memories/{id}/status` → state + non-sensitive reason codes.

4. **Report (useful work).** `POST /v1/reports {title, text, source_ids}` with a **publishing-run token only**; gateway re-checks public-source provenance + secret scan → `{report_url}`. `GET /reports/{id}` renders the public note.

5. **Config the agent client needs:** `GATEWAY_BASE_URL`, the run-token env var, the run's `source_ids`, and the report base URL.

**Fixtures (proposing `contracts/fixtures.json` as shared truth):** canary `DEMO_SECRET_ALPHA_2026`; Alpha record `Avery Example / 100 Example Lane / Service Plan A`; Beta bearer `DEMO_SECRET_BETA_2026`; sources `src-public-runbook` (everyone), `src-alpha-private` (alpha only).

**Allowed vs blocked scenarios I will drive:**
- Blocked: Agent A handoff mixing the runbook + the canary → must be `QUARANTINED`; B search + guessed-ID lookup return nothing; the same write via a Python HTTP script gets the same decision; a direct Senso write without the backend key fails.
- Allowed: A reads an approved Alpha record; B reads the public runbook and publishes a source-linked note.

Please confirm points 1–5 or propose edits. If anything is undecided, I proceed against the mock and mark it clearly as not-yet-integrated.
