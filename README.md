# Agent Memory Gateway

Permission-aware shared memory for autonomous agents. Sensitive information keeps its
source access restrictions, credentials never enter searchable memory, and agents can
still retrieve useful approved knowledge. Built for the Cyberdefense Hackathon (SF, 2026-10-09).

**Stack:** AWS (API Gateway/Lambda/DynamoDB/Secrets Manager) gateway · AkashML inference ·
Senso knowledge storage · ClickHouse audit telemetry. All demo data is synthetic.

## Layout
- `docs/spec/` — authoritative design + contract
- `contracts/` — shared fixtures + interface (integrator-owned)
- `agents/` — Agents A/B + gateway client (Florian)
- `demo/` — CRM fixture, demo runner, output page (Florian)
- `gateway/`, `infra/` — AWS gateway (Ash)
- `analytics/` — ClickHouse telemetry (separately assigned)
- `tests/` — acceptance checks
- `docs/handoffs/` — cross-agent handoffs

## Coordination
Two coding agents build in parallel: **Ash + Codex** (gateway/auth/admission) and
**Florian + Claude** (agents/demo). Interface changes go through an issue or PR.
See the "Agent integration coordination" issue.

## Protected gateway (Ash)

The real gateway lives in `gateway/`. It enforces run-scoped identity, whole-write
quarantine, inherited source permissions, provider-version checks, and immediate
authoritative revocation. `demo/mock_gateway.py` remains Florian's local mock.

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q
python3 tests/test_gateway_contract.py
```

The pytest suite tests the real policy service with controlled provider responses
and AWS emulation. The separate existing contract script tests the mock. Neither
is a claim of live sponsor integration.

See [AWS deployment](infra/README.md), [gateway integration contract](contracts/gateway-integration.md),
[telemetry setup](analytics/README.md), and [Ash's handoff](docs/handoffs/ash.md).
Florian owns `adapters/senso_adapter.py`; its trusted wrapper is packaged into the gateway.

Real writes remain `INGESTING` until the status endpoint confirms Senso readiness.
Agents receive only freshly issued run tokens. Keep `.env`, `.gateway-admin.json`,
and the generated operator files under `build/` out of source control.
