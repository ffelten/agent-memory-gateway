"""Verification gate and the pinned, append-only rule set.

A proposed rule becomes enforceable only through `verify()`; the rule set only grows
(`RuleSet.append`), and every rule is hash-chained so the gateway can pin one head
value and refuse any file that was edited, reordered or truncated.
"""
import hashlib
import json
import re
import subprocess
import sys

VERIFIER = "guardloop.gate.verify/1"
MAX_PATTERN = 300
PROBE_TIMEOUT_S = 2.0
_FORBIDDEN = (
    (r"\\[1-9]|\(\?P=", "backreference"),
    (r"\(\?<?[=!]", "lookaround"),
    (r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)[+*{]", "nested_quantifier"),
    (r"\(\?[aiLmsux]", "inline_flags"),
)
# Inputs that make backtracking regexes explode; run in a subprocess with a hard timeout.
_PROBE = r"""
import re, sys, time
rx = re.compile(sys.stdin.read(), re.IGNORECASE)
for probe in ("a" * 20000 + "!", "1" * 20000 + "x", "-a" * 10000, "a:" * 10000 + "@", "x " * 10000):
    rx.search(probe)
"""


def compile_rule(pattern):
    return re.compile(pattern, re.IGNORECASE)


def verify(pattern, item, mutations, legit):
    """Return (accepted, evidence, reasons). `item` and `mutations` are attack dicts."""
    reasons = []
    if not isinstance(pattern, str) or not 4 <= len(pattern) <= MAX_PATTERN:
        return False, {}, ["invalid_length"]
    for rx, name in _FORBIDDEN:
        if re.search(rx, pattern):
            reasons.append(name)
    try:
        rx = compile_rule(pattern)
    except re.error:
        return False, {}, reasons + ["does_not_compile"]
    if reasons:
        return False, {}, reasons
    if rx.search("") or rx.search(" ") or rx.search("a"):
        return False, {}, ["matches_trivial_input"]
    for secret in {m["secret"] for m in [item, *mutations]}:
        if any(secret[i:i + 8] in pattern for i in range(len(secret) - 7)):
            return False, {}, ["contains_literal_secret"]
    try:
        subprocess.run([sys.executable, "-I", "-c", _PROBE], input=pattern, text=True,
                       capture_output=True, timeout=PROBE_TIMEOUT_S, check=True)
    except subprocess.TimeoutExpired:
        return False, {}, ["catastrophic_backtracking"]
    except subprocess.CalledProcessError:
        return False, {}, ["probe_failed"]

    def hits(a):
        return bool(rx.search(a["title"]) or rx.search(a["text"]))
    if not hits(item):
        reasons.append("misses_original_attack")
    missed = sum(not hits(m) for m in mutations)
    if missed:
        reasons.append(f"misses_{missed}_of_{len(mutations)}_mutations")
    false_pos = [i for i, note in enumerate(legit) if rx.search(note)]
    if false_pos:
        reasons.append("matches_legitimate_note: " + legit[false_pos[0]][:80])
    evidence = {"original": 1, "mutations": len(mutations), "legit_checked": len(legit)}
    return not reasons, evidence, reasons


def _canonical(rule):
    body = {k: rule[k] for k in ("id", "family", "pattern", "origin", "verifier", "evidence", "proposer")}
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


class RuleSet:
    """Append-only verified rules. `head` is the value the gateway pins."""

    GENESIS = "0" * 64

    def __init__(self, rules=()):
        self.rules = list(rules)

    @property
    def head(self):
        return self.rules[-1]["chain"] if self.rules else self.GENESIS

    def append(self, rule):
        rule = dict(rule, id=f"gr-{len(self.rules) + 1:03d}")
        rule["chain"] = hashlib.sha256((self.head + _canonical(rule)).encode()).hexdigest()
        self.rules.append(rule)
        return rule

    def to_json(self):
        return {"version": len(self.rules), "head": self.head, "verifier": VERIFIER, "rules": self.rules}

    @classmethod
    def load(cls, data, pinned_head):
        """Rebuild and check the chain; refuse anything that does not end at `pinned_head`."""
        prev = cls.GENESIS
        for i, rule in enumerate(data["rules"]):
            if rule["id"] != f"gr-{i + 1:03d}":
                raise ValueError("rule order changed")
            expect = hashlib.sha256((prev + _canonical(rule)).encode()).hexdigest()
            if rule["chain"] != expect:
                raise ValueError("rule chain broken")
            compile_rule(rule["pattern"])
            prev = expect
        if prev != pinned_head:
            raise ValueError("rule set does not match pinned head")
        return cls(data["rules"])

    def scanner(self, builtin):
        """Gateway-compatible scanner(title, text) -> bool: built-in checks OR any learned rule."""
        compiled = [compile_rule(r["pattern"]) for r in self.rules]

        def scan(title, text):
            return bool(builtin(title, text) or any(rx.search(v) for rx in compiled for v in (title, text)))
        return scan

    def first_match(self, title, text):
        for rule in self.rules:
            rx = compile_rule(rule["pattern"])
            if rx.search(title) or rx.search(text):
                return rule["id"]
        return None
