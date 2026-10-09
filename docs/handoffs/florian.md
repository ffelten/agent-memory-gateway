# Handoff — Florian's lane (agents, demo, Senso adapter)

Updated 2026-10-09, ~15:50 PT. Everything below is on `main`; no open PRs.

## Live artifacts
- **Repo:** https://github.com/ffelten/agent-memory-gateway
- **Demo page (Akash):** http://67s3r5os51agp42v6tfsi3fhsc.ingress.zencloud.eu, generated from the full live run (PR #15).
- **Gateway (AWS, Ash):** https://yrx2yjzb7h.execute-api.us-east-1.amazonaws.com
- **Submission draft:** `docs/SUBMISSION.md` (form text, sponsor lines, 3-minute video script).

## What's in this lane
| Path | What |
|---|---|
| `agents/` | Agents A/B on AkashML (`akash_client.py`), gateway client with INGESTING polling, bypass script |
| `adapters/senso_adapter.py` | Senso adapter; runs inside Ash's gateway via `gateway/senso_bridge.py` |
| `demo/run_demo.py` | Demo runner; mock mode, admin mode (mints fresh runs), pre-minted token mode (`GATEWAY_USE_PREMINTED=1`) |
| `demo/mint_runs.py` | Trusted harness: mints three scoped runs into `.gateway-runs.env` |
| `demo/crm_fixture.py` | Synthetic CRM fixture |
| `demo/akash-demo.yaml`, `demo/DEPLOY.md` | Akash deployment of the demo page |
| `contracts/fixtures.json` | Shared fixture manifest |

## How to run
```bash
cd ~/Documents/hackathon && git pull
set -a; . ./.env; set +a

# tests
uv run python3 tests/test_gateway_contract.py      # mock contract suite
uv run python3 agents/test_agents.py               # agent e2e (forces stub model)
uv run python3 adapters/test_senso_adapter.py      # live Senso round trip
uv run --no-project --with-requirements requirements-dev.txt python -m pytest -q   # gateway suite

# full demo against the live AWS gateway (~40s, mints fresh runs each time)
GATEWAY_BASE_URL=https://yrx2yjzb7h.execute-api.us-east-1.amazonaws.com uv run python3 demo/run_demo.py
open demo/output/index.html
```
Last verified on a clean checkout of `main`: contract suite pass, agent suite pass, gateway suite 228 passed / 1 skipped, Senso live pass, full live demo 10/10.

## Refresh the public page after a new run
Commit `demo/output/index.html` to `main` through a PR, then restart the Akash container so it re-fetches the page:
```bash
curl -sX PATCH https://console-api.akash.network/v1/deployments/1791580242440 \
  -H "x-api-key: $AKASH_API_KEY" -H "Content-Type: application/json" \
  -d '{"data":{"services":{"web":{"env":{"REV":"<next number>"}}}}}'
```

## Secrets (local only, all gitignored, never in the repo)
- `.env`: AkashML, Akash Console, Senso, ClickHouse credentials
- `.gateway-admin.json`: gateway admin bearer (from Ash)
- `.gateway-runs.env`: last minted run tokens
- `~/Downloads/ash-gateway-credentials.env`: the copy shared with Ash; delete once he has loaded it

## Known limits
- The demo runner's unprotected comparison runs against the local mock, and its output says so.
- No in-browser live/visual view; the demo is the story page plus the terminal run.
- The Senso key is a full-org CLI key that expires 2026-10-16.

## Remaining
| Item | Owner |
|---|---|
| Record the 3-minute video (script in `docs/SUBMISSION.md`) | Florian |
| Add Ash's email to the form; submit at tokensand.com/cyberhack/submit before 4:30 PT | Florian + Ash |
| Tick prizes: Top overall (Pi), Senso, ClickHouse, Akash | Florian |
| Close the Akash deployment after judging (`DELETE /v1/deployments/1791580242440`) | Florian |

No fresh run tokens are needed: the admin key is local and each live run mints its own.
