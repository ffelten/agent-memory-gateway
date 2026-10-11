"""Guard loop: the gate's refusals, the pinned rule set, and enforcement inside the real gateway service.

    .venv/bin/python -m pytest tests/test_guard_loop.py -q
"""
import copy
import json

import pytest

from gateway.scanner import has_secret
from guardloop import loop
from guardloop.attacks import BENIGN_TRAFFIC, LEGIT_HELDOUT, LEGIT_VERIFY, attempt, mutate, redact
from guardloop.gate import RuleSet, verify
from tests.security.test_policy import rig  # noqa: F401  real Gateway policy service fixture

GH = loop.STUB_CANDIDATES["github_pat"][0]


def _case(family="github_pat"):
    item = attempt(family, "t-orig")
    return item, [attempt(family, f"t-mut-{k}") for k in range(8)]


def test_every_synthetic_attack_except_bearer_evades_builtin_scanner():
    for family in loop.ROUNDS[-1]:
        caught = [has_secret(a["title"], a["text"]) for a in (attempt(family, f"b-{i}") for i in range(6))]
        assert all(caught) if family == "bearer" else not any(caught), family
    assert not any(has_secret("", note) for note in LEGIT_VERIFY + LEGIT_HELDOUT)
    assert not any(has_secret(t, x) for t, x in BENIGN_TRAFFIC)
    assert not {x for _, x in BENIGN_TRAFFIC} & set(LEGIT_VERIFY)


def _shape(s):
    return "".join("d" if c.isdigit() else "U" if c.isupper() else "l" if c.islower() else c for c in s)


def test_mutate_keeps_shape_and_prefix_but_changes_the_value():
    for family in loop.ROUNDS[-1]:
        item = attempt(family, "mut-src")
        for k in range(4):
            m = mutate(item, f"mut-{k}")
            assert m["secret"] != item["secret"] and _shape(m["secret"]) == _shape(item["secret"]), family
            assert m["secret"] in m["title"] + m["text"] and item["secret"] not in m["title"] + m["text"]
    gh = mutate(attempt("github_pat", "mut-src"), "mut-1")
    assert "ghp_" + gh["secret"][:0] in gh["title"] + gh["text"]
    db = mutate(attempt("db_url", "mut-src"), "mut-1")
    assert "://svc_" in db["title"] + db["text"] and ":" + db["secret"] + "@" in db["title"] + db["text"]


def test_proposer_never_sees_the_secret_value():
    for family in loop.ROUNDS[-1]:
        item = attempt(family, "redact")
        title, text = redact(item)
        assert item["secret"] not in title + text and "<<SECRET shape=" in title + text


def test_gate_accepts_a_shape_rule():
    item, muts = _case()
    ok, evidence, reasons = verify(GH, item, muts, LEGIT_VERIFY)
    assert ok and not reasons and evidence["mutations"] == 8


@pytest.mark.parametrize("pattern, reason", [
    (r"\bpass\s?phrase\s+(?:is|was)\s+\S{6,}", "matches_legitimate_note"),
    (r"(a+)+$", "nested_quantifier"),
    (r"(\w)\1", "backreference"),
    (r"(?=ghp_)ghp_\w+", "lookaround"),
    (r"[a-z ]{0,9}", "matches_trivial_input"),
    (r"ghp_[a-f]{36}", "misses"),
])
def test_gate_rejects(pattern, reason):
    item, muts = _case("passphrase" if "phrase" in pattern else "github_pat")
    ok, _, reasons = verify(pattern, item, muts, LEGIT_VERIFY)
    assert not ok and any(reason in r for r in reasons), reasons


def test_gate_rejects_memorised_secret():
    item, muts = _case()
    ok, _, reasons = verify("ghp_" + item["secret"], item, muts, LEGIT_VERIFY)
    assert not ok and reasons == ["contains_literal_secret"]


def _ruleset():
    rules = RuleSet()
    for fam in ("github_pat", "db_url"):
        rules.append({"family": fam, "pattern": loop.STUB_CANDIDATES[fam][0], "origin": {"round": 1},
                      "verifier": "test", "evidence": {}, "proposer": "stub"})
    return rules


def test_pinned_rule_set_refuses_edits_reorders_and_truncation():
    rules = _ruleset()
    data = json.loads(json.dumps(rules.to_json()))
    assert len(RuleSet.load(data, rules.head).rules) == 2
    edited = copy.deepcopy(data)
    edited["rules"][0]["pattern"] = "never-matches-anything"
    truncated = dict(copy.deepcopy(data), rules=data["rules"][:1])
    reordered = dict(copy.deepcopy(data), rules=data["rules"][::-1])
    for bad in (edited, truncated, reordered):
        with pytest.raises(ValueError):
            RuleSet.load(bad, rules.head)


def test_stub_loop_auditor_findings_drive_rules():
    proposer, auditor, memory = loop.Proposer(stub=True), loop.Auditor(stub=True), loop.LocalMemory()
    rules, rows = loop.run(proposer, auditor, memory, log=lambda *_: None)
    rates = [r["blocked_at_door"] / r["leak_attempts"] for r in rows]
    assert rates == sorted(rates) and rates[-1] == 1.0
    assert all(r["benign_blocked_at_door"] == 0 for r in rows)
    assert all(r["auditor_missed"] == 0 and r["auditor_false_flags"] == 0 for r in rows)
    assert rows[0]["auditor_found"] == rows[0]["admitted_leaks"] > 0
    assert len(rules.rules) == sum(len(r["rules_accepted"]) for r in rows) > 0
    assert all(r["rules_accepted"] == [] for r in rows[-1:])  # nothing left to learn once rates hit 1.0
    assert any(c["family"] == "passphrase" for r in rows for c in r["candidates_rejected"])
    assert not any(has_secret(n["title"], n["text"]) for n in memory.read_all())


def test_auditor_drops_findings_that_are_not_substrings():
    class Fake(loop.Auditor):
        def __init__(self):
            self.stub, self.calls = False, 0
            self.chat = lambda *_a, **_k: {"content": 'prose {"findings":[{"id":"a","secret":"ghp_realvalue1"},'
                                           '{"id":"a","secret":"dup-dup-1"},{"id":"b","secret":"nope-nope"},'
                                           '{"id":"c","secret":"abc"},{"id":"zz","secret":"ghp_realvalue1"}]} tail'}
    notes = [{"id": "a", "title": "t", "text": "key ghp_realvalue1 end"}, {"id": "b", "title": "t", "text": "x"},
             {"id": "c", "title": "t", "text": "abc"}]
    assert Fake().sweep(notes, {}) == [{"id": "a", "secret": "ghp_realvalue1"}]


def test_learned_rules_quarantine_in_real_gateway_service(rig):  # noqa: F811
    app, _, provider, call, tokens = rig
    leak = attempt("github_pat", "gateway-e2e")
    body = {"title": leak["title"], "text": leak["text"]}
    _, before, _ = call("POST", "/v1/memories", tokens["alpha"], body, "k-before")
    assert before["state"] != "QUARANTINED"  # built-in scanner alone misses this shape
    calls_before = len(provider.calls)
    app.scanner = _ruleset().scanner(has_secret)
    _, after, _ = call("POST", "/v1/memories", tokens["alpha"], body, "k-after")
    assert after["state"] == "QUARANTINED" and after["reason_codes"] == ["SECRET_MATCH"]
    assert len(provider.calls) == calls_before  # quarantined before any provider ingestion
