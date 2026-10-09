# Agent Memory Gateway — working agreement

Read `docs/spec/agent-memory-gateway-design.md` and `docs/handoffs/` before work.
The user's current ownership update overrides the older spec table: **Florian owns Senso integration as well as agents/scenario**.

- Ash/Codex: `gateway/`, `infra/`, `analytics/`, `tests/security/`, shared contract integration.
- Florian/Claude: `adapters/senso_adapter.py`, `agents/`, `demo/`, scenario tests and presentation.
- The Senso adapter executes inside the trusted gateway; ownership never grants demo agents backend credentials.
- Preserve `agents/gateway_client.py` request formats. Real admission is asynchronous at the provider: poll status while INGESTING.
- Never add a protection-disable request option to the gateway. The synthetic baseline is a separate trusted harness.
- Keep private candidate text, queries, tokens, and provider errors out of audit events, logs, and error responses.
- Current permissions and revocations are authoritative on every read; provider search is not authorization.
- Work on separate branches. Propose contract changes in a checked-in handoff; do not overwrite another owner's lane.
- Do not claim live AWS/sponsor integration based on local fakes or Florian's mock gateway tests.
- Use Python 3.12. Gateway checks: `.venv/bin/python -m pytest tests/security tests/storage tests/analytics -q`.

Shared records and provider interface are documented in `contracts/gateway-integration.md`.
