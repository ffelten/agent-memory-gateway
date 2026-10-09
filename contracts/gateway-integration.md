# Gateway integration contract — v1

Existing HTTP routes and fixture IDs in the specification remain unchanged.
This file records the implementation boundary. Deployment and verification evidence is summarized in `docs/handoffs/ash.md`.

## Ownership and runtime

Florian implements `adapters/senso_adapter.py`; Ash wires it into Lambda via `gateway/senso_bridge.py`. Never import the adapter or expose its keys in `agents/`. No production fallback from Senso to fake storage. Missing provider configuration yields a bounded dependency error and leaves content unreadable.

The internal provider interface is `gateway/provider.py`. The bridge adapts Florian's existing `ingest`, `search_context`, and `delete` methods; it reads node metadata through his adapter's request primitive to establish actual content versions. Search receives only a nonempty server-authorized content-ID list with `require_scoped_ids=True`. Gateway checks current authoritative permissions and provider version again before returning the immutable admitted document text for the retrieved ID. Provider text cannot substitute another document's content under an authorized ID.

## Local interfaces

Store interface (Python dict records):
- `get(kind, key) -> dict | None`: strongly consistent in DynamoDB.
- `put(kind, key, value, expected_revision=None, create_only=False) -> dict`: returned `_rev` increments; stale conditional writes raise `Conflict`.
- `list(kind) -> list[dict]`: paginate; final authorization uses `get` again.
- `create_candidate(memory, idempotency_id) -> (record, created)`: transactionally reserve idempotency and save PENDING; same key/hash returns original; different hash raises `IdempotencyConflict`.
- Memory records use `memory_id`, `content_hash`, `state`, `_rev`; tenant/principal/run are server derived.
- Token lookup key is SHA-256 of a high-entropy opaque token; store no plaintext tokens.

## HTTP compatibility

`POST /v1/admin/runs` returns `{run_id, token, source_ids}`. Admin bearer is separately configured in Secrets Manager; never put it in an agent environment. Templates remain `tpl-alpha`, `tpl-beta`, `tpl-beta-publish` for existing clients.

`POST /v1/memories` returns HTTP 202 with `{memory_id,state,reason_codes,trace_id}`. Poll the creator-only status endpoint until APPROVED or QUARANTINED. INGESTING/PENDING cannot be retrieved. Do not assume the mock's instant approval. Reasons include SECRET_MATCH, PROVENANCE_UNKNOWN, SCANNER_ERROR, SOURCE_ACCESS_DENIED, PROVIDER_UNAVAILABLE, PROVIDER_VERSION_CHANGED, TIMEOUT.

`POST /v1/memories/search` returns `{results:[{memory_id,text,source_urls,application_version}],trace_id}`. Empty scope returns empty results without calling Senso. Unauthorized direct memory/status IDs return 404; forbidden source access returns 403.

`POST /v1/reports` remains public-publishing-run only and returns `{report_id,report_url}`. A public source must be publicly publishable, not merely organization-readable. The full run source set must be public. No private source can be omitted to launder provenance.

## Source model

Use one demo organization tenant; Alpha/Beta are customer access boundaries within it, identified by principal/source permissions. Registered sources carry `source_id,tenant_id,text,sha256,url,retrieved_at,readers,public,active`. `readers=["*"]` means all principals in that organization. `public=true` additionally permits public publication. Run records freeze source IDs, source hashes, and the intersection audience. Reads require current source permission plus the frozen audience and source-set compatibility.

## Deployed integration

Florian's adapter is merged and the gateway's real AWS/Senso round trip passed, including version metadata checks. Factory is `gateway.senso_bridge:create_provider`; config fields `api_key`, `folder_id`, optional fixed official `base_url`. Deployed base URL is `https://yrx2yjzb7h.execute-api.us-east-1.amazonaws.com`. Existing agent environment names are `GATEWAY_BASE_URL`, `AGENT_A_TOKEN`, `AGENT_B_TOKEN`, and `AGENT_B_PUB_TOKEN`; each process receives only its own run token. Tokens travel through the secure handoff, never this contract or Git. The full live Akash/baseline demonstration remains a separate integration check.
