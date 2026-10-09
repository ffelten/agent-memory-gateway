# Ash / Codex — protected gateway handoff

## Current scope

Branch: `codex/gateway`. Based on main including Florian's `adapters/senso_adapter.py`.

Deployed endpoint: `https://yrx2yjzb7h.execute-api.us-east-1.amazonaws.com`

Review/integrate [PR #10](https://github.com/ffelten/agent-memory-gateway/pull/10). Ash's fork contains the branch because direct pushes to the upstream repository are unavailable.

Ash owns the deployed security boundary, authoritative DynamoDB state, public report checks, and audit ingestion. Florian owns Senso API mapping, Akash agents, CRM, baseline/demo runner, and trace display. The adapter runs inside Lambda with backend credentials, never inside agent processes.

## Interface answers for issue #1

- Existing client HTTP request shapes and fixture source IDs are accepted unchanged.
- Templates: `tpl-alpha` / `eng-alpha`; `tpl-beta` / `eng-beta`; `tpl-beta-publish` / `eng-beta`.
- Mint fresh tokens through `POST /v1/admin/runs` from the trusted harness only. It returns `run_id`, `token`, `source_ids`. Admin bearer lives in a local ignored operator file and its SHA-256 hash in the gateway config secret. Never use the mock's default tokens against AWS.
- Existing agents already read `GATEWAY_BASE_URL`, `AGENT_A_TOKEN`, `AGENT_B_TOKEN`, and `AGENT_B_PUB_TOKEN`; keep these names. Inject only the appropriate token into each agent process. Admin configuration never enters those processes.
- A create response is HTTP 202, normally `INGESTING` or `QUARANTINED`. Poll the creator-only status route with bounded backoff; allow up to 90 seconds for Senso. Only `APPROVED` is readable.
- Reason codes include `SECRET_MATCH`, `PROVENANCE_UNKNOWN`, `SCANNER_ERROR`, `TIMEOUT`, `SOURCE_ACCESS_DENIED`, `PROVIDER_UNAVAILABLE`, `PROVIDER_VERSION_CHANGED`. No rejected text is echoed.
- Direct unauthorized memory IDs and another run's status return 404. Source denials return 403. Reports require a fresh publishing run and the full run provenance must be public.
- Public output uses safe escaped HTML at `/reports/{report_id}`. Report links include selected citations; all contributing run sources remain permission dependencies.
- Alpha and Beta are customer boundaries inside one demo organization tenant. Source ACLs and frozen output audience enforce isolation.

## Senso bridge

`gateway/senso_bridge.py` adapts Florian's existing methods. It inspects `/org/kb/nodes/{node_id}` for actual `content.id`, `content.version_num`, and `content.processing_status`. Missing ready-version metadata fails closed. Both the local bridge and the deployed AWS gateway passed real Senso ingestion, readiness, scoped retrieval, and version checks on October 9. Live public and private gateway records also passed immediate local revocation checks.

Requests use his custom User-Agent, bounded timeouts, fixed HTTPS origin, and no credential-bearing redirects. Search is scoped; empty scope makes no provider request. Senso selects/ranks document IDs; the gateway returns each matched document's immutable admitted text after current authorization/version checks, so a provider cannot inject another document's text under an authorized ID.

Revocation updates DynamoDB first. Scheduled maintenance retries deletion of revoked provider nodes; provider deletion delay never grants read access.

## Telemetry

`analytics/schema.sql` defines the ClickHouse table; `analytics/README.md` describes setup and queries. Field is `timestamp`; display it with `timestamp AS ts`. Use `FINAL` when querying the replacing table to deduplicate delivery retries. Events contain allowlisted metadata only. The telemetry IAM role can read/write only the `event` DynamoDB partition, not candidate bodies.

The deployed analytics Lambda exported all 36 smoke events with zero failures; ClickHouse read-back matched all 16 metadata fields, timestamps, and timings. All 27 distinct trace IDs in the 40 smoke checks were present. Decisions were 23 ALLOW, 11 DENY, and 2 QUARANTINE. These are actual gateway events, not replay-generated rows. The earlier isolated ClickHouse probe is separate from this smoke trace set.

Measured `total_ms` for this small smoke run ranged from 0.575 to 3046.791; 13 events included measured Senso work. This is observed evidence for these fixtures, not a load or latency guarantee. Both one-minute retry schedules are now enabled after successful deployed worker verification. The schedule-only CloudFormation update completed successfully; `build/schedule-evidence.json` records the actual enabled rules.

## Demo integration work for Florian

`demo/run_demo.py` currently launches two mock gateways and uses static mock tokens. Point the protected path at AWS, mint real runs, and preserve the isolated live-Senso baseline separately. Do not add an unprotected route or request flag to the AWS gateway.

The useful public runbook memory must be created under the public-only run, independently of Alpha's handoff. The registered public source comes from an allowlisted fetch of `https://docs.senso.ai/docs/knowledge-base` with raw and registered-content hashes. Use that actual source when generating the note; the mock's invented firmware procedure is not the deployed source.

Ash's ignored `build/run-tokens.json` contains the issued runs and source IDs. For the existing agents, `build/agent-alpha.env`, `build/agent-beta.env`, and `build/agent-beta-publish.env` each contain the endpoint and only that run's token, with mode 0600. Transfer these through the same secure channel used for credentials. Do not load the repo's sponsor `.env` into agent subprocesses. Start the publishing agent in a fresh context and environment.

The mock suite is useful client regression coverage but does not establish AWS, live Senso, Akash, ClickHouse, MCP, or runtime isolation acceptance. MCP must call the same HTTPS routes. Agent runtime isolation and the full actual before/after scenario remain Florian's integration checks.

The demo sandbox must exclude operator files and AWS home credentials as well as backend environment variables. Filtering environment variables alone does not establish filesystem isolation.

## Verification and remaining work

- Existing mock contract suite: passed before gateway changes.
- Gateway/security/storage/bootstrap/telemetry suite: 228 passed, 1 backend-specific skip at final local review; later live results are recorded in the PR.
- AWS profile/account verified; runtime roles are project scoped.
- All 40 deployed AWS gateway smoke check entries passed: authentication, source ACLs, whole-write quarantine, idempotency, public publication and escaping, real Senso public/private ingestion and retrieval, Alpha/Beta separation, immediate revocation, and metadata-only outbox checks. Sanitized operator evidence is in ignored `build/live-smoke.json`.
- Real ClickHouse schema creation, metadata insert, and read-back passed. The deployed Lambda then delivered all 36 live gateway events, verified by read-back; `build/aws-analytics-evidence.json` records the result.
- The live quarantined canary record has no registered Senso node/content/version metadata. This supports the gateway admission result; it is not a provider-wide content inventory.
- Bounded CloudWatch inspection covered all three functions and their two shared/distinct log groups. No log events were present in the smoke window under the configured ERROR/WARN levels, so this does not exercise every error-logging path. `build/aws-privacy-evidence.json` records the scope.
- The deployed maintenance Lambda submitted both revoked nodes for deletion and cleared both retry flags; strongly consistent reads confirmed both records remained REVOKED. Senso deletion may settle asynchronously. Evidence: `build/maintenance-evidence.json`.
- All 25 IAM policy simulation checks passed, including analytics event-partition access and denial of memory/source access. Gateway/analytics secret grants remain separate. Simulation validates the deployed policy configuration; it does not prove isolation of Florian's agent runtime. Evidence: `build/iam-boundary-evidence.json`.

Credentials are never committed. The operator file `.gateway-admin.json`, local `.env`, deployment output, evidence, and issued run tokens under `build/` are ignored.
