# Ash / Codex — protected gateway handoff

## Current scope

Branch: `codex/gateway`. Based on main including Florian's `adapters/senso_adapter.py`.

Ash owns the deployed security boundary, authoritative DynamoDB state, public report checks, and audit ingestion. Florian owns Senso API mapping, Akash agents, CRM, baseline/demo runner, and trace display. The adapter runs inside Lambda with backend credentials, never inside agent processes.

## Interface answers for issue #1

- Existing client HTTP request shapes and fixture source IDs are accepted unchanged.
- Templates: `tpl-alpha` / `eng-alpha`; `tpl-beta` / `eng-beta`; `tpl-beta-publish` / `eng-beta`.
- Mint fresh tokens through `POST /v1/admin/runs` from the trusted harness only. It returns `run_id`, `token`, `source_ids`. Admin bearer lives in a local ignored operator file and its SHA-256 hash in the gateway config secret. Never use the mock's default tokens against AWS.
- Use `GATEWAY_BASE_URL` and per-run token configuration. Suggested names for the trusted harness are `GATEWAY_TOKEN_ALPHA`, `GATEWAY_TOKEN_BETA`, `GATEWAY_TOKEN_BETA_PUB`; inject only the appropriate token into each agent process. Admin configuration never enters those processes.
- A create response is HTTP 202, normally `INGESTING` or `QUARANTINED`. Poll the creator-only status route with bounded backoff; allow up to 90 seconds for Senso. Only `APPROVED` is readable.
- Reason codes include `SECRET_MATCH`, `PROVENANCE_UNKNOWN`, `SCANNER_ERROR`, `TIMEOUT`, `SOURCE_ACCESS_DENIED`, `PROVIDER_UNAVAILABLE`, `PROVIDER_VERSION_CHANGED`. No rejected text is echoed.
- Direct unauthorized memory IDs and another run's status return 404. Source denials return 403. Reports require a fresh publishing run and the full run provenance must be public.
- Public output uses safe escaped HTML at `/reports/{report_id}`. Report links include selected citations; all contributing run sources remain permission dependencies.
- Alpha and Beta are customer boundaries inside one demo organization tenant. Source ACLs and frozen output audience enforce isolation.

## Senso bridge

`gateway/senso_bridge.py` adapts Florian's existing methods. It inspects `/org/kb/nodes/{node_id}` for actual `content.id`, `content.version_num`, and `content.processing_status`. Missing ready-version metadata fails closed. The gateway bridge passed a real Senso ingest → readiness → scoped retrieval → version check → deletion request on October 9. The AWS-to-Senso integration is verified separately after deployment.

Requests use his custom User-Agent, bounded timeouts, fixed HTTPS origin, and no credential-bearing redirects. Search is scoped; empty scope makes no provider request. Senso selects/ranks document IDs; the gateway returns each matched document's immutable admitted text after current authorization/version checks, so a provider cannot inject another document's text under an authorized ID.

Revocation updates DynamoDB first. Scheduled maintenance retries deletion of revoked provider nodes; provider deletion delay never grants read access.

## Telemetry

`analytics/schema.sql` defines the ClickHouse table; `analytics/README.md` describes setup and queries. Field is `timestamp`; display it with `timestamp AS ts`. Use `FINAL` when querying the replacing table to deduplicate delivery retries. Events contain allowlisted metadata only. The telemetry IAM role can read/write only the `event` DynamoDB partition, not candidate bodies.

Workers remain disabled until sponsor configuration and live insert/query checks are complete. Outbox persistence still occurs while workers are disabled.

## Demo integration work for Florian

`demo/run_demo.py` currently launches two mock gateways and uses static mock tokens. Point the protected path at AWS, mint real runs, and preserve the isolated live-Senso baseline separately. Do not add an unprotected route or request flag to the AWS gateway.

The useful public runbook memory must be created under the public-only run, independently of Alpha's handoff. The registered public source comes from an allowlisted official fetch with raw and registered-content hashes.

The mock suite is useful client regression coverage but does not establish AWS, live Senso, Akash, ClickHouse, MCP, or runtime isolation acceptance. MCP must call the same HTTPS routes. Agent runtime isolation and the full actual before/after scenario remain Florian's integration checks.

## Verification and remaining work

- Existing mock contract suite: passed before gateway changes.
- Gateway/security/storage/bootstrap/telemetry suite: 228 passed, 1 backend-specific skip at final local review; later live results are recorded in the PR.
- AWS profile/account verified; runtime roles are project scoped.
- AWS deployment, gateway live smoke, Senso version round trip, and ClickHouse export are recorded below when actually run. Do not infer success from this handoff.

Credentials are never committed. The operator file `.gateway-admin.json`, local `.env`, deployment output, evidence, and issued run tokens under `build/` are ignored.
