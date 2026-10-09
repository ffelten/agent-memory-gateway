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
