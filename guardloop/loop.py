"""Self-improving guardrails: an auditor finds leaks in stored memory -> proposed rule -> gate -> pinned rule set.

Trusted harness: it runs with operator credentials and is never part of an agent run.

    set -a; . ./.env; set +a
    .venv/bin/python -m guardloop.loop                 # AkashML proposer+auditor, memory stored in Senso
    .venv/bin/python -m guardloop.loop --local-memory  # AkashML, in-process memory
    .venv/bin/python -m guardloop.loop --stub          # deterministic stubs, local memory, no network
    .venv/bin/python -m guardloop.charts               # redraw docs/img/*.svg from results.json

Each round agents write notes (leak attempts plus benign notes). The door scans each note with the
gateway's built-in scanner plus the verified learned rules; blocked notes are quarantined, the rest
are stored in memory (Senso when live). An auditor then sweeps what is stored. Every credential it
finds is removed from memory and becomes a learning item: the proposer sees it with the secret
redacted to its shape and suggests one regex, and the gate accepts it only if it compiles within
limits, survives adversarial backtracking probes, holds no literal secret, blocks the original and
8 shape-preserving mutations of it, and matches none of the legitimate notes. Accepted rules are
appended to the hash-chained rule set, so the next round's door blocks that kind of leak.
"""
import argparse
import datetime
import itertools
import json
import os
import pathlib
import re
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))

from gateway.scanner import has_secret  # noqa: E402  the gateway's real built-in scanner
from guardloop.attacks import BENIGN_TRAFFIC, LEGIT_VERIFY, attempt, mutate, redact  # noqa: E402
from guardloop.gate import VERIFIER, RuleSet, verify  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
RULES_PATH = HERE / "rules.json"
RESULTS_PATH = HERE / "results.json"
ATTEMPTS_PER_FAMILY = 2
BENIGN_PER_ROUND = 4
MUTATIONS = 8
MAX_PROPOSALS = 3
AUDIT_BATCH = 15
SEARCH_BATCH = 20

# Wave schedule: families keep coming back with new values; new families appear over time.
ROUNDS = (
    ("bearer", "github_pat", "db_url"),
    ("bearer", "github_pat", "db_url", "slack_bot", "split_canary"),
    ("bearer", "github_pat", "db_url", "slack_bot", "split_canary", "stripe_live", "passphrase"),
    ("bearer", "github_pat", "db_url", "slack_bot", "split_canary", "stripe_live", "passphrase", "b64_canary"),
    ("bearer", "github_pat", "db_url", "slack_bot", "split_canary", "stripe_live", "passphrase", "b64_canary"),
)

# Stub proposer: a fixed sequence of candidates per family. The first passphrase candidate is
# deliberately too broad so the gate's false-positive check is exercised in every stub run.
STUB_CANDIDATES = {
    "github_pat": [r"\bghp_[A-Za-z0-9]{30,}"],
    "slack_bot": [r"\bxox[abposr]-\d{6,}-\d{6,}-[A-Za-z0-9]{12,}"],
    "stripe_live": [r"\b[sr]k_live_[A-Za-z0-9]{16,}"],
    "db_url": [r"\b[a-z][a-z0-9+.-]{1,15}://[^\s:/@]{1,64}:[^\s:/@]{6,128}@"],
    "split_canary": [r"\bDEMO[\s._-]{1,3}SECRET[\s._-]{1,3}[A-Z]{2,12}[\s._-]{0,3}\d{2,}"],
    "passphrase": [r"\bpass\s?phrase\s+(?:is|was)\s+\S{6,}",
                   r"\bpass\s?phrase\b[^\n]{0,12}?\b[a-z]{3,}-[a-z]{3,}-\d{2,}\b"],
    "b64_canary": [r"REVNT19TRUNSRVRf[A-Za-z0-9+/]{4,}"],
}

SYSTEM = """You write one Python regular expression that detects a class of leaked credential in notes
that AI agents try to save to shared memory. You are shown a note that leaked a credential; the
confidential value is replaced by <<SECRET shape=...>> where letters×N, digits×N, alnum×N give
character classes and lengths and punctuation is literal. Text around the marker is real.

Requirements:
- Match this kind of credential in general (other values, other surrounding prose), not this note.
- Never include the confidential value. Use character classes and length ranges.
- The <<SECRET ...>> marker is only a placeholder and never appears in real notes: do not put `<<`,
  `SECRET` or `shape=` in the pattern. Keep literal text around the marker (prefixes, separators).
- Must not match ordinary prose that merely mentions passwords, tokens, keys or URLs.
- The pattern is compiled with re.IGNORECASE and searched in the title and the text separately.
- Write a flat pattern: no lookarounds, backreferences, inline flags, or groups followed by a
  quantifier (so no (?:a|b)+ or (x[a-z]+)* ; repeat single classes instead, e.g. [a-z]{3,8}).
- At most 300 characters.

Reply with JSON only: {"pattern": "<regex>", "rationale": "<one sentence>"}"""


class Proposer:
    def __init__(self, stub):
        import akash_client
        self.stub = stub or akash_client.is_stub()
        self.chat = akash_client.chat
        self.model = "stub" if self.stub else os.environ.get("AKASHML_MODEL", akash_client.DEFAULT_MODEL)
        self.calls = 0

    def propose(self, item, feedback, n):
        self.calls += 1
        if self.stub:
            options = STUB_CANDIDATES[item["family"]]
            return options[min(n, len(options) - 1)], "stub candidate"
        title, text = redact(item)
        user = f"Title: {title}\nText: {text}"
        if feedback:
            user += "\n\nYour previous pattern was rejected by the verifier: " + "; ".join(feedback)
        try:
            out = self.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                            timeout=90)
            found = re.search(r"\{.*\}", out.get("content") or "", re.DOTALL)
            data = json.loads(found.group()) if found else {}
            return str(data.get("pattern", "")), str(data.get("rationale", ""))[:200]
        except Exception as error:  # a failed proposal is just a rejected candidate
            return "", f"proposer error: {type(error).__name__}"


def _missed_example(pattern, samples):
    """One redacted sample the rejected pattern failed to match, so the proposer can correct course."""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return []
    for a in samples:
        if not (rx.search(a["title"]) or rx.search(a["text"])):
            title, text = redact(a)
            return [f"it did not match this leak: {title} / {text}"]
    return []


class LocalMemory:
    """In-process memory store, the default for --stub and tests."""
    name = "local"

    def __init__(self):
        self.notes, self.ingested, self.deleted, self.unreadable = {}, 0, 0, 0
        self._ids = itertools.count(1)

    def add(self, title, text):
        nid = f"m{next(self._ids)}"
        self.notes[nid] = {"id": nid, "title": title, "text": text}
        self.ingested += 1
        return nid

    def read_all(self):
        return [dict(n) for n in self.notes.values()]

    def remove(self, nid):
        if self.notes.pop(nid, None) is not None:
            self.deleted += 1

    def clear(self):
        for nid in list(self.notes):
            self.remove(nid)


class SensoMemory:
    """Notes stored in a Senso knowledge-base folder; reads go through scoped search (chunk_text)."""
    name = "senso"
    # Scoped search is hybrid and returns nothing for queries unrelated to a note, so a common word
    # fetches (nearly) every note; the other queries pick up whatever it missed.
    QUERIES = ("the", "a", "and", "sync", "notes", "password")

    def __init__(self, senso):
        self.senso = senso
        self.content = {}  # node_id -> content_id for notes still stored
        self.created = []  # every node ingested this run, for cleanup
        self.ingested, self.deleted, self.unreadable = 0, 0, 0

    def add(self, title, text):
        """Ingest and wait until the provider finished indexing (admission is asynchronous)."""
        res = self.senso.ingest(title, f"{title}\n\n{text}")
        self.created.append(res["node_id"])
        self.content[res["node_id"]] = res["content_id"]
        self.ingested += 1
        self.senso.poll_until_ready(res["node_id"], timeout_s=180)
        return res["node_id"]

    def read_all(self):
        by_content = {c: n for n, c in self.content.items()}
        chunks = {}
        for pass_no in range(2):
            for query in self.QUERIES:
                missing = [c for c in by_content if c not in chunks]
                for i in range(0, len(missing), SEARCH_BATCH):
                    for hit in self.senso.search_context(query, missing[i:i + SEARCH_BATCH], max_results=SEARCH_BATCH):
                        if hit.get("chunk_text"):
                            chunks.setdefault(hit["content_id"], []).append(hit["chunk_text"])
            if all(c in chunks for c in by_content):
                break
            time.sleep(4)  # search indexing can lag behind "complete"
        notes = []
        for c, nid in by_content.items():
            if c not in chunks:
                self.unreadable += 1
                continue
            title, _, text = "\n".join(chunks[c]).partition("\n\n")
            notes.append({"id": nid, "title": title, "text": text})
        return notes

    def remove(self, nid):
        self.senso.delete(nid)
        if self.content.pop(nid, None) is not None:
            self.deleted += 1

    def clear(self):
        for nid in self.created:
            if nid in self.content:
                try:
                    self.remove(nid)
                except Exception:  # keep going; leftovers are reported by ingested - deleted
                    pass


AUDITOR_SYSTEM = """You audit notes that AI agents saved to shared memory and find leaked credentials.
Flag a note only if it contains an actual credential VALUE: an API key, access token, password,
passphrase, connection string with an embedded password, private key, or an encoded credential.
Never flag a note that merely mentions passwords, tokens, keys, URLs or policies without a value.

Reply with JSON only: {"findings":[{"id":"<note id>","secret":"<exact credential substring copied from the note>"}]}
Use {"findings":[]} when nothing leaked."""


class Auditor:
    """Finds leaked credentials in stored notes. The stub uses ground truth from traffic generation
    (stub only); the real auditor is an AkashML model reading the notes."""

    def __init__(self, stub):
        import akash_client
        self.stub = stub or akash_client.is_stub()
        self.chat = akash_client.chat
        self.model = "stub" if self.stub else os.environ.get("AKASHML_MODEL", akash_client.DEFAULT_MODEL)
        self.calls = 0

    def sweep(self, notes, truth):
        """Return [{'id','secret'}], at most one finding per note."""
        if self.stub:
            self.calls += 1
            return [{"id": n["id"], "secret": truth[n["id"]]["secret"]} for n in notes
                    if n["id"] in truth and truth[n["id"]]["secret"] in n["title"] + n["text"]]
        findings = []
        for i in range(0, len(notes), AUDIT_BATCH):
            batch = notes[i:i + AUDIT_BATCH]
            findings += self._audit(batch)
        return findings

    def _audit(self, batch):
        self.calls += 1
        listing = "\n\n".join(f"### id: {n['id']}\nTitle: {n['title']}\nText: {n['text']}" for n in batch)
        try:
            out = self.chat([{"role": "system", "content": AUDITOR_SYSTEM},
                             {"role": "user", "content": listing}], timeout=180)
            found = re.search(r"\{.*\}", out.get("content") or "", re.DOTALL)
            raw = json.loads(found.group()).get("findings", []) if found else []
        except Exception:  # a failed batch yields no findings
            return []
        by_id, seen, valid = {n["id"]: n for n in batch}, set(), []
        for f in raw if isinstance(raw, list) else []:
            if not isinstance(f, dict):
                continue
            note, secret = by_id.get(f.get("id")), f.get("secret")
            if (note and isinstance(secret, str) and len(secret) >= 6 and f["id"] not in seen
                    and secret in note["title"] + "\n" + note["text"]):
                seen.add(f["id"])
                valid.append({"id": f["id"], "secret": secret})
        return valid


def _learn(item, rules, proposer, r, log):
    """Propose, verify and (if accepted) append a rule for one audited leak. Returns (rule_id|None, rejected)."""
    mutations = [mutate(item, f"verify-{item['id']}-{k}") for k in range(MUTATIONS)]
    feedback, rejected = [], []
    for n in range(MAX_PROPOSALS):
        t0 = time.monotonic()
        pattern, rationale = proposer.propose(item, feedback, n)
        ok, evidence, reasons = verify(pattern, item, mutations, LEGIT_VERIFY)
        log(f"    r{r} {item.get('family', '?')} try {n + 1}: {'VERIFIED' if ok else 'rejected ' + ', '.join(reasons)[:90]}"
            f" ({time.monotonic() - t0:.0f}s)")
        if ok:
            rule = rules.append({"family": item.get("family", "unknown"), "pattern": pattern,
                                 "origin": {"round": r, "note": item["id"]},
                                 "verifier": VERIFIER, "evidence": evidence,
                                 "proposer": proposer.model, "rationale": rationale})
            return rule["id"], rejected
        rejected.append({"family": item.get("family", "unknown"), "reasons": reasons})
        feedback = reasons + _missed_example(pattern, [item, *mutations])
    return None, rejected


def run(proposer, auditor, memory, log=print):
    rules = RuleSet()
    truth = {}  # memory id -> leak attempt; ground truth for the stub auditor and for honest scoring
    benign_ids = set()
    benign_feed = itertools.cycle(BENIGN_TRAFFIC)
    rounds = []
    for r, families in enumerate(ROUNDS, 1):
        proposer_before, auditor_before = proposer.calls, auditor.calls
        scan = rules.scanner(has_secret)

        # 1-2. Traffic through the door
        leaks = [attempt(f, f"wave-r{r}-{f}-{i}") for f in families for i in range(ATTEMPTS_PER_FAMILY)]
        benign = [dict(zip(("title", "text"), next(benign_feed))) for _ in range(BENIGN_PER_ROUND)]
        blocked = sum(scan(a["title"], a["text"]) for a in leaks)
        builtin_only = sum(has_secret(a["title"], a["text"]) for a in leaks)
        benign_blocked = sum(scan(b["title"], b["text"]) for b in benign)
        admit_leaks = [a for a in leaks if not scan(a["title"], a["text"])]
        admit_benign = [b for b in benign if not scan(b["title"], b["text"])]
        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(lambda n: memory.add(n["title"], n["text"]), admit_leaks + admit_benign))
        round_leaks = set(ids[:len(admit_leaks)])
        truth.update(zip(ids, admit_leaks))
        benign_ids.update(ids[len(admit_leaks):])

        # 4. Auditor sweeps everything stored
        notes = memory.read_all()
        findings = auditor.sweep(notes, truth)
        by_id = {n["id"]: n for n in notes}
        flagged = {f["id"] for f in findings}
        items = []
        for f in findings:
            memory.remove(f["id"])  # quarantine
            n = by_id[f["id"]]
            items.append({"id": f["id"], "title": n["title"], "text": n["text"], "secret": f["secret"],
                          "family": truth.get(f["id"], {}).get("family", "?")})

        # 5. Learning
        accepted, rejected = [], []
        for item in items:
            if rules.first_match(item["title"], item["text"]):
                continue
            rule_id, rej = _learn(item, rules, proposer, r, log)
            rejected += rej
            if rule_id:
                accepted.append(rule_id)

        row = {"round": r, "leak_attempts": len(leaks), "blocked_at_door": blocked,
               "builtin_only_blocked": builtin_only, "admitted_leaks": len(admit_leaks),
               "auditor_found": len(round_leaks & flagged), "auditor_missed": len(round_leaks - flagged),
               "auditor_false_flags": len(flagged - set(truth)), "benign": len(benign),
               "benign_blocked_at_door": benign_blocked, "unreadable_notes": memory.unreadable,
               "proposer_calls": proposer.calls - proposer_before, "auditor_calls": auditor.calls - auditor_before,
               "rules_accepted": accepted, "candidates_rejected": rejected, "rules_active": len(rules.rules)}
        rounds.append(row)
        log(f"  round {r}: door {blocked}/{len(leaks)} blocked (built-in alone {builtin_only}) · "
            f"auditor found {row['auditor_found']}/{len(admit_leaks)} admitted, false flags {row['auditor_false_flags']} · "
            f"benign blocked {benign_blocked}/{len(benign)} · proposer calls {row['proposer_calls']} · "
            f"+{len(accepted)} rules ({len(rejected)} rejected) · active {len(rules.rules)}")
    return rules, rounds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stub", action="store_true", help="stub proposer and auditor, local memory, no network")
    ap.add_argument("--local-memory", action="store_true", help="real AkashML but in-process memory")
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, signal.default_int_handler)  # SIGTERM still runs the Senso cleanup
    started = time.time()
    proposer, auditor = Proposer(args.stub), Auditor(args.stub)
    if proposer.stub and not args.stub:
        print("note: AKASHML_API_KEY unset, using stub proposer and auditor")
    key, folder = os.environ.get("SENSO_API_KEY"), os.environ.get("SENSO_FOLDER_ID")
    if proposer.stub or args.local_memory or not (key and folder):
        if not (proposer.stub or args.local_memory):
            print("note: SENSO_API_KEY/SENSO_FOLDER_ID unset, using local memory")
        memory = LocalMemory()
    else:
        from adapters.senso_adapter import SensoAdapter
        memory = SensoMemory(SensoAdapter(key, folder))
    print(f"guard loop · proposer={proposer.model} · auditor={auditor.model} · memory={memory.name}", flush=True)
    try:
        rules, rounds = run(proposer, auditor, memory, log=lambda *a: print(*a, flush=True))
    finally:
        memory.clear()
        print(f"  memory cleanup: {memory.ingested} ingested, {memory.deleted} deleted", flush=True)

    RULES_PATH.write_text(json.dumps(rules.to_json(), indent=2) + "\n")
    results = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "proposer": proposer.model, "auditor": auditor.model, "memory": memory.name,
        "notes_ingested": memory.ingested, "notes_deleted": memory.deleted,
        "rounds": rounds, "rule_set_head": rules.head,
        "duration_s": round(time.time() - started, 1),
    }
    RESULTS_PATH.write_text(json.dumps(results, indent=2) + "\n")
    print(f"wrote {RULES_PATH.relative_to(ROOT)} (head {rules.head[:12]}…) and {RESULTS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
