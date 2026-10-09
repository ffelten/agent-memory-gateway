# Agent Memory Gateway Hackathon Specification

We are building permission-aware shared memory for autonomous agents. Sensitive information keeps its source access restrictions, credentials never enter searchable memory, and agents can still retrieve useful approved knowledge. The gateway runs on AWS. The demo uses synthetic data and real sponsor API calls.

This specification is for Ash and Florian. Success means demonstrating the same incident with protection disabled and enabled, proving the difference in actual retrieval and customer API responses, and completing a useful public action. Submission is due October 9 2026 at 4:30 PM Pacific.

## Product outcome and demonstration

Agent A handles a Customer Alpha incident. Its handoff memory mixes a useful troubleshooting procedure with the synthetic bearer credential DEMO_SECRET_ALPHA_2026. Agent B works for Customer Beta and has no Alpha access. In the isolated unprotected demonstration, B retrieves the leaked credential and uses it to read Alpha's synthetic customer name, address, and purchase history. Its own Beta credential fails against Alpha first, proving the access difference.

With protection enabled, the same candidate memory is quarantined before ingestion. B cannot retrieve the credential through search or direct memory lookup. Alpha's separately approved sensitive memory remains readable by A and inaccessible to B. B can still retrieve an independently approved public runbook and publish a source-linked troubleshooting note to a team-owned webpage. That publication runs in a fresh context containing only public approved sources.

The short pitch is: We stop an agent's private customer context from becoming another employee's shared memory, while preserving useful knowledge and proving every decision.

### Actors and fixtures

| Actor or source | Allowed access | Exact purpose |
| --- | --- | --- |
| Agent A as engineer Alpha | Public runbook and Alpha records | Resolve the Alpha incident and create its handoff |
| Agent B as engineer Beta | Public runbook and Beta records | Retrieve permitted knowledge and complete useful work |
| Agent B public publishing run | Public sources only | Publish the troubleshooting note without private context |
| Public runbook source | Everyone in the demo organization | Independently admitted procedure with source URL and content hash |
| Alpha private source | Alpha-authorized employees only | Synthetic name Avery Example, address 100 Example Lane, and purchase Service Plan A |

Fetch the public procedure from an official technical documentation URL on an operator-maintained allowlist. The trusted harness registers the fetched bytes, URL, retrieval time, and SHA-256 hash. The demo CRM is a team-owned HTTPS fixture service; its bearer credentials intentionally model a legacy customer API. No real customer records or production credentials enter the demonstration.

### Approaches and selected scope

| Approach | Benefit | Tradeoff |
| --- | --- | --- |
| Synchronous admission checks | Smallest reproducible implementation | The caller waits for bounded local checks |
| Private pending queue with asynchronous checks | Accepts writes quickly while keeping them unreadable | Requires worker coordination and timeout handling |
| Publish first and remove later | Simple monitoring demonstration | Allows disclosure before removal and cannot meet our prevention promise |

Build synchronous deterministic admission plus asynchronous Senso compilation first. Preserve an unreadable pending state so a later model review can run asynchronously without exposing data. Asynchronous semantic review is an extension, not a prerequisite for the hackathon. The MVP has create, search, direct lookup, and revoke; edits, arbitrary imports, and human declassification are not exposed.

## AWS architecture and sponsor responsibilities

Use a Python 3.12 Lambda policy gateway behind an Amazon API Gateway HTTP API. DynamoDB stores the authoritative source registry, principal and run permissions, memory states, revocations, and an audit outbox. Secrets Manager holds Senso, ClickHouse, and model credentials with least-privilege IAM grants to the relevant trusted services. Deploy with a small AWS SAM template. These are proposed implementation defaults; the security decisions above are agreed.

Agents receive opaque tokens scoped to their assigned principal and run. The gateway hashes and verifies those tokens against server configuration. The trusted harness creates runs; agents cannot create a new run or select another principal. The demo agent runtime has no AWS credentials, Senso keys, DynamoDB access, gateway filesystem mount, or container control socket. MCP adapters and Python scripts use the same HTTPS routes and token checks.

| Service | Responsibility | Data it receives |
| --- | --- | --- |
| AWS API Gateway | HTTPS routing to the policy Lambda | Requests with authenticated gateway tokens |
| AWS Lambda | Authentication, permission checks, admission, retrieval filtering, revocation, publication | Candidate text and trusted source metadata |
| AWS DynamoDB | Authoritative state and restricted pending storage | Memory metadata; pending bodies readable only by gateway role |
| AWS Secrets Manager | Backend credentials | Sponsor keys; never returned through an agent tool |
| Senso | Ingest and retrieve admitted documents | Public or explicitly permitted sensitive knowledge; never blocked credentials |
| AkashML | Inference for both autonomous agents | Public documentation and synthetic authorized incident context |
| ClickHouse | Decision analytics and measured latency | Allowlisted metadata fields, without raw text, queries, tokens, addresses, or model prompts |

AWS hosts the gateway; Senso, AkashML, and ClickHouse provide the three core sponsor integrations. Gateway keys remain inaccessible even when an agent uses curl or Python. A direct Senso request without a valid backend key must fail. This proves the credential boundary; it does not claim AWS API Gateway alone controls all agent internet traffic.

Raw synthetic Alpha context may reach Agent A's Akash inference. The demo is not a claim that all plaintext remains inside AWS. Production deployment requires an approved model-processing boundary and private-data handling appropriate to that environment.

## Admission and access policy

Authenticate before processing content. Derive the tenant, principal, run permissions, and source restrictions on the server. Agent-supplied actor IDs, customer labels, source permissions, or approval flags cannot grant access.

Each trusted run has a fixed source set and an output audience no broader than the intersection of those sources' authorized readers. A run that can read Alpha data produces Alpha-restricted memory even when the generated summary omits obvious personal information. A public publishing run starts with a fresh conversation and cannot fetch private sources. Unknown provenance or an inconsistent run binding leaves the candidate quarantined. This conservative policy applies to registered sources and the controlled runtime; it is not complete tracking of arbitrary out-of-band inputs.

For admission, inspect the title and body locally before any Senso ingestion, embedding, analytics export, or optional classification call. Detect the exact synthetic credential canary and common credential-shaped patterns. A secret match quarantines the entire candidate. A benign private record may be admitted under its inherited restrictions. Missing permissions, invalid provenance, scanner errors, and timeouts never broaden an audience or publish a candidate. Names and addresses are not reliably detectable by regex; source restrictions carry the main personal-information protection.

Persist an immutable application memory version and content hash. The gateway exposes no provider edit route. Source permission changes and revocations must affect future reads immediately through authoritative state, independently of provider deletion. Explicit denial always overrides any semantic risk score. Models may propose review, but cannot grant permission or declassify a memory.

### Memory states

| State | Meaning | Searchable by agents |
| --- | --- | --- |
| PENDING | Private candidate awaiting checks | No |
| QUARANTINED | Secret, provenance issue, uncertainty, or check failure | No |
| INGESTING | Policy passed; Senso compilation incomplete | No |
| APPROVED | Policy passed and provider compilation complete | Only for authorized readers |
| REVOKED | Removed from authoritative access set | No |

Creation saves PENDING privately, evaluates policy, and transitions to QUARANTINED or INGESTING. Senso readiness polling promotes an unchanged permitted candidate to APPROVED only after compilation completes. Recheck permission and memory revision during promotion. A timeout leaves it unreadable. There is no user-facing approval button for secret-containing candidates in this MVP.

For every search and direct lookup, check tenant, current source access, run binding, APPROVED state, and revocation. Use strongly consistent DynamoDB reads for the final authorization decision. Derive a nonempty approved content ID allowlist on the server; an empty list returns no results locally. Send require_scoped_ids true to Senso and filter returned passages before the model sees text. Reject unknown content IDs and changed provider versions. Recheck authoritative access immediately before returning the response.

Revocation removes the memory from the authoritative allowlist before requesting provider deletion. A new read after the revocation commit must be denied. Data already returned to an agent cannot be recalled; do not promise erasure from its previous conversation or an in-flight response already delivered.

## API and adapter contracts

Every agent request uses Authorization Bearer with its run-scoped opaque token. The gateway derives identity from that token. Invalid authentication returns 401. Unauthorized direct memory IDs return 404 to avoid revealing record existence. JSON bodies are limited to 32 KiB in the demo. Unknown identity or policy fields are rejected.

| Route | Request | Response and policy |
| --- | --- | --- |
| POST /v1/admin/runs | Admin-only template_id and principal_id | Run ID and scoped token; fixed source and output permissions |
| GET /v1/sources/{source_id} | Assigned run token | Authorized source text; deny sources outside the run |
| POST /v1/memories | title, text; Idempotency-Key header | 202 with memory_id, state, reason_codes, trace_id; no echo of sensitive text |
| GET /v1/memories/{memory_id}/status | Creator run token | State and non-sensitive reason codes; triggers bounded readiness refresh |
| POST /v1/memories/search | query, max_results up to 5 | results containing memory_id, text, source URLs, application version; only allowed passages |
| GET /v1/memories/{memory_id} | Assigned run token | Approved authorized content or 404; same policy as search |
| POST /v1/admin/memories/{memory_id}/revoke | Admin-only reason code | REVOKED after authoritative commit; provider deletion queued separately |
| POST /v1/reports | title, text, source_ids | Publishing-run token only; recheck public-source provenance and secret scan; return report URL |
| GET /reports/{report_id} | Public reader | Escaped plain text or safe rendered Markdown; public approved note only |

Idempotency is bound to principal, run, and candidate hash; reusing the same key with different content returns 409. Pending or quarantined bodies have no retrieval endpoint. Invalid requests return 400, oversized bodies 413, and unavailable dependencies a bounded error. No error response or log echoes a secret. Public report requests with private sources are denied even if the text appears harmless.

Senso uses base URL https://apiv2.senso.ai/api/v1 and X-API-Key authentication. Ingest with POST /org/kb/raw using title, text, and kb_folder_node_id. Retain both the node ID and content ID. Poll GET /org/kb/nodes/{node_id} and its content.processing_status until complete. Retrieve passages using POST /org/search/context with query, max_results, server-derived content_ids, and require_scoped_ids true.

Node IDs organize or delete documents; content IDs scope search. Keep them separate. Use immutable provider documents for admitted application versions. Confirm content.version_num remains the registered version before retrieval; refuse drift. Only forward chunks whose returned content_id is approved. A successful search or ingestion response is not proof that a particular employee is authorized.

Senso moves and deletes settle asynchronously. DELETE /org/kb/nodes/{node_id} may require retry while compilation is active. Poll GET /org/kb/sync-status for structural completion, but always enforce local revocation first. Use folder-restricted read and write keys behind the gateway where available. All adapter response schemas must be confirmed with one real round trip before dependent work.

## Reproducible demo and acceptance checks

Run the same synthetic candidate through two isolated namespaces. The intentionally unprotected namespace is accessible only to the demo harness and contains synthetic fixtures. Protection cannot be disabled by an agent-supplied field. Capture one generated candidate, freeze its hash, and reuse those exact bytes in the comparison; do not depend on two model runs accidentally generating identical leaks.

1. Show B's Beta credential failing to access Alpha's fixture endpoint. Show that A's credential succeeds for Alpha.
2. Run A's real Akash task. The incident handoff wrapper preserves the generated summary plus the original incident context, causing the credential to enter the memory candidate. Describe this persistence behavior accurately.
3. Submit that candidate in the unprotected namespace, wait for Senso readiness, and show B's actual retrieval response containing the canary. Use the retrieved credential against the fixture API and show the synthetic Alpha records returned.
4. Submit the identical candidate through the protected gateway. Show QUARANTINED and an empty B retrieval response. Repeat the write with a Python HTTP script and show the same decision. Show direct Senso access failing without its backend key.
5. Show A retrieving an independently admitted Alpha record while B's search and guessed-ID lookup cannot retrieve it. B then retrieves the public runbook and publishes the public-source-only note. Open the resulting webpage and its source links.
6. Show ClickHouse decisions and measured timings for the trace. Keep the raw synthetic response evidence separate from metadata-only analytics.

| Required check | Passing evidence |
| --- | --- |
| Same input comparison | Protected and unprotected candidate SHA-256 hashes match |
| Actual breach baseline | B retrieval includes canary; using that credential returns Alpha fixture records |
| Secret admission | Candidate quarantined; no protected Senso ingestion for its content hash |
| Permission preservation | A reads approved Alpha memory; B search and direct ID yield no Alpha text |
| Script parity | MCP and Python writes produce the same protected decision |
| Missing provenance or failed scanner | Candidate remains unreadable; no publication or provider ingestion |
| Provider compilation delay | INGESTING memory cannot be retrieved before completion |
| Empty scope or stale provider result | No unscoped query; unknown or revoked returned content is discarded |
| Revocation | New reads fail immediately after gateway state commit, before Senso deletion settles |
| Useful work | B retrieves clean runbook and creates a real public note with valid source links |
| Audit privacy | Canary, fixture address, bearer tokens, raw queries, and prompts absent from analytics and logs |
| Runtime isolation | Agent cannot read backend keys or use its IAM role to access gateway storage |

Display gate_ms, senso_ms, total_ms, decision counts, and completed useful tasks. Record real measured values and label any replay-generated analytics rows. Do not claim million-event scale or fixed low latency without running the workload. The first acceptance target is zero forbidden bytes in B's protected retrieval responses on these named fixtures; this is a test result, not a universal detection rate.

## Team ownership and coordination

Proposed ownership assigns Ash the security boundary and Florian the agent experience. Confirm these assignments with each other before parallel implementation. Maintain one shared contract file and one fixture manifest so both coding agents agree on fields, statuses, source IDs, and canary values.

| Owner | Workstream | Deliverable |
| --- | --- | --- |
| Ash and gateway coding agent | AWS deployment, token identity, source permissions, memory states, Senso adapter, read and revoke policy | Working gateway plus security acceptance checks |
| Florian and agent coding agent | Akash A/B runners, synthetic CRM, controlled handoff, Python bypass attempt, public note and demo UI | One repeatable before and after demonstration |
| Florian and telemetry agent if capacity allows | Metadata schema, ClickHouse ingestion, trace view | Actual sponsor events, latency breakdown, no raw sensitive values |
| Ash and Florian together | Contract freeze, integration, raw-response verification, recording, submission | Accessible repository, demo video, sponsor usage and team contact information |

Suggested repository boundaries are gateway/, agents/, demo/, analytics/, contracts/, infra/, and tests/. The gateway agent owns gateway/ and infra/; the agent experience owner owns agents/ and demo/; telemetry owns analytics/. Only the integrator changes contracts/. Do not have two agents editing the same paths. Publish contract changes as a short message naming the changed field, reason, and required consumer update; wait for consumer acknowledgement before breaking changes.

Before deeper UI work, validate one real Akash inference, one Senso ingest to scoped retrieval round trip, one ClickHouse insert to query round trip, and one deployed AWS route. Missing sponsor access is an immediate booth escalation, not a simulated integration. Freeze the route and fixture contracts after these checks. Each owner reports the commit or file change, exact check run, blocker, and next dependency. Share credentials through local secret configuration rather than chat or repository files.

## Critical work and presentation traps

Prioritize actual identity enforcement, whole-write quarantine, scope checks on both search and direct ID, isolated backend credentials, reproducible raw-response evidence, and the three live sponsor integrations. Add the minimal UI only after the protected and unprotected paths work. Defer Kubernetes, Istio, custom model training, automatic declassification, generalized encoded-secret detection, a production IAM console, and complex multi-agent orchestration.

Aim for an end-to-end integration by 3:15 PM Pacific, security and demo checks by 3:45 PM, recording by 4:00 PM, and submission preparation by 4:15 PM. Keep the 4:30 PM deadline as a hard constraint. These are build targets, not a report of completed work.

Audit events contain event_id, trace_id, tenant_id, principal_id, run_id, memory_id, application_version, operation, transport, decision, reason_code, policy_version, timings, and timestamp. Use opaque IDs. The DynamoDB outbox durably records this metadata; ClickHouse is the analytics sink. If ClickHouse is unavailable, queue metadata for retry without weakening authorization. ClickHouse monitoring can trigger an administrative revoke, but detecting a leak after retrieval cannot turn it into prevention.

Present the story in this order: B cannot access Alpha; leaked memory gives B a working credential; the gateway blocks the same write; B still completes the useful public task; the trace proves the decisions. The judge should understand the breach before seeing the architecture.

Expect these questions: Why not redact everything? Authorized users still need legitimate sensitive information. Why not use an LLM alone? Permissions are server rules and cannot depend on a model verdict. What stops Python bypass? Backend credentials and storage access are isolated; HTTP uses the same policy. What if Senso deletion is slow? Authoritative revocation removes access before deletion. Is this entirely novel? Memory-defense research already exists; our specific demonstration connects customer access, source permissions, script enforcement, useful actions, and actual sponsor evidence.

Detection covers the chosen canary and named patterns; it does not reliably recognize all paraphrased, encoded, or fragmented secrets. The gateway does not undo prior disclosure, control unrelated private data supplied outside the registered runtime, or protect against an administrator deliberately using backend keys. State these boundaries when relevant to a judge's question.

## Implementation references

- AWS API Gateway Lambda integration: https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-integrations-lambda.html
- AWS Lambda and Secrets Manager: https://docs.aws.amazon.com/lambda/latest/dg/with-secrets-manager.html
- Senso ingestion and queued deletion: https://docs.senso.ai/docs/knowledge-base
- Senso search scope and content identifiers: https://docs.senso.ai/docs/concepts
- Senso folder and key permissions: https://docs.senso.ai/docs/permissions
- AkashML chat completion API: https://akashml.com/docs/api-reference/openai/post-v1-chat-completions
- ClickHouse integration documentation: https://clickhouse.com/docs
- OWASP authorization guidance: https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html
- Prior art to read before claiming novelty: MINJA https://arxiv.org/abs/2503.03704 and MemSentry https://arxiv.org/abs/2609.08747

