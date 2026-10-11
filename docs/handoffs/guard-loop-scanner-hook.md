# Handoff → Ash: load verified learned rules into the gateway scanner

**From:** Florian · **Status:** proposal; `gateway/` is unchanged · **Context:** [Guard loop](../guard-loop.md)

`guardloop/` produces a **pinned, append-only,
hash-chained rule set** (`guardloop/rules.json`). It is learned from secrets an auditor agent found in
Senso memory, and each rule has passed a verification gate. Today the rules are enforced only in tests. `tests/test_guard_loop.py` injects them into
the real `Gateway` through its existing `scanner=` hook and shows a quarantine (`SECRET_MATCH`)
before any provider call. Running them in the deployed gateway needs a small change in your lane.

## Proposed change (about 20 lines, `gateway/` only)

1. Package `guardloop/rules.json` with the Lambda (or put it in Secrets Manager / SSM).
2. Pin the expected head in trusted config: `GUARD_RULES_HEAD=<rules.json "head">`.
3. At cold start:

```python
from guardloop.gate import RuleSet          # stdlib only; or copy the 30-line loader into gateway/
from gateway.scanner import has_secret

rules = RuleSet.load(json.load(open(RULES_PATH)), os.environ["GUARD_RULES_HEAD"])
gateway = Gateway(store, provider, ..., scanner=rules.scanner(has_secret))
```

`RuleSet.load` recomputes the hash chain and raises if any rule was edited, reordered or removed,
or if the file doesn't end at the pinned head. **On any load error, fail closed.** Do not
fall back to the built-in scanner silently. Either refuse to start, or keep the built-in scanner
and emit a `SCANNER_ERROR`-style alarm. Pick one and document it.

## Invariants this keeps

- **Rules can only block more.** A learned rule is OR-ed with `has_secret`. Nothing in the rule set can
  admit a write that the built-in scanner quarantines, and there is no disable option.
- **No secret values in rules.** The gate rejects any pattern that contains an 8-character window of
  a secret it was trained on. The proposer only sees the secret as a shape (`letters×5-digits×4`).
- **Bounded cost.** Patterns are capped at 300 chars. Backreferences, lookarounds, inline flags and
  nested quantifiers are rejected. Every pattern must pass adversarial backtracking probes under a
  2 s hard timeout, which keeps your 200 ms scanner budget safe.
- **Telemetry stays metadata-only.** If you want per-rule counts in ClickHouse, add a
  `rule_id` column (for example, `gr-003`). Never log the matched text.

## How to check it

```sh
.venv/bin/python -m pytest tests/test_guard_loop.py -q   # gate, tamper refusal, real-service quarantine
.venv/bin/python -m guardloop.loop --stub --no-senso     # regenerate rules.json deterministically
```
