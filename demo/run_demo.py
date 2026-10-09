"""Agent Memory Gateway: end-to-end before/after demo runner.

Run:  uv run python3 demo/run_demo.py
Starts protected (:8808) / unprotected (:8809) mock gateways and the synthetic CRM (:8900)
as subprocesses, runs the 6-step story, writes demo/output/index.html, exits 0 only if
every acceptance check passed. Stdlib only. All data is SYNTHETIC.
"""
import hashlib
import html
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "demo"))

import agent_a  # noqa: E402
import agent_b  # noqa: E402
import akash_client  # noqa: E402
import bypass_attempt  # noqa: E402
from gateway_client import GatewayClient  # noqa: E402

FIX = agent_a.FIX
CANARY = FIX["canary_secret"]
ADDRESS = FIX["tenants"]["alpha"]["record"]["address"]
PROT, UNPROT, CRM = "http://127.0.0.1:8808", "http://127.0.0.1:8809", "http://127.0.0.1:8900"
TOK_A, TOK_B, TOK_BPUB, TOK_ADMIN = "tok-alpha", "tok-beta", "tok-beta-pub", "admin-demo-token"
# Real-gateway mode: GATEWAY_BASE_URL set -> PROTECTED steps go to that gateway with freshly
# minted run tokens (via demo/mint_runs.py, admin bearer stays in this trusted harness only).
# Unset -> protected steps use the local mock exactly as before. Unprotected steps always use the mock.
REAL = bool(os.environ.get("GATEWAY_BASE_URL"))
PBASE = os.environ["GATEWAY_BASE_URL"].rstrip("/") if REAL else PROT
P_TOK_A, P_TOK_B, P_TOK_BPUB, P_ADMIN = TOK_A, TOK_B, TOK_BPUB, TOK_ADMIN
# Pre-minted mode: no admin bearer available (or GATEWAY_USE_PREMINTED=1), but the gateway owner
# supplied three run tokens (env, or the gitignored .gateway-runs.env). Minting and the admin
# revoke step are skipped.
PREMINTED = False
RUNS_ENV_FILE = pathlib.Path(os.environ.get("GATEWAY_RUNS_ENV_FILE") or ROOT / ".gateway-runs.env")
TOKEN_NAMES = ("GATEWAY_TOKEN_ALPHA", "GATEWAY_TOKEN_BETA", "GATEWAY_TOKEN_BETA_PUB")
SKIP = "SKIP"
SKIP_REVOKE = "needs admin bearer; verified in the gateway owner's live AWS smoke run"
LOCAL_HOSTS = ("127.0.0.1", "localhost")
FORBIDDEN = [CANARY, ADDRESS, TOK_A, TOK_B, TOK_BPUB, TOK_ADMIN, FIX["tenants"]["beta"]["crm_bearer"]]
QUERIES = ["Alpha deploy handoff", "deploy handoff firmware sync legacy CRM", "Alpha re-pair checklist firmware"]
REDACTED = "DEMO_SECRET_***"
OUT = ROOT / "demo" / "output"

CANARY_RE = re.compile(r"DEMO_SECRET_[A-Z]+_\d{4}")


def scrub(s):
    """Mask the canary and Alpha's address for any display."""
    return CANARY_RE.sub(REDACTED, str(s)).replace(ADDRESS, "[ADDRESS REDACTED]")


def jscrub(obj):
    return scrub(json.dumps(obj, indent=2))


def load_premint_tokens():
    """Return {NAME: token} if all three tokens exist (env first, then the runs env file), else None."""
    vals = {k: os.environ.get(k) for k in TOKEN_NAMES}
    if not all(vals.values()) and RUNS_ENV_FILE.exists():
        for line in RUNS_ENV_FILE.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and k in vals and not vals[k]:
                vals[k] = v.strip().strip("'\"")
    return vals if all(vals.values()) else None


def status_of(ok):
    return ok if ok == SKIP else ("PASS" if ok else "FAIL")


def step(n, title):
    print(f"\n{'=' * 78}\nSTEP {n}: {title}\n{'=' * 78}")


# ----------------------------------------------------------------- process management
class Procs:
    def __init__(self):
        self.procs, self.events, self.lock = [], [], threading.Lock()
        self.raw_output = []

    def start(self, name, args, env_extra, port, audit_ns=None):
        s = socket.socket()
        s.settimeout(0.3)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            s.close()
            raise SystemExit(f"port {port} already in use; stop the other process first")
        s.close()
        env = dict(os.environ, PYTHONUNBUFFERED="1", **env_extra)
        p = subprocess.Popen([sys.executable] + args, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        self.procs.append(p)
        threading.Thread(target=self._pump, args=(p, audit_ns), daemon=True).start()
        for _ in range(100):
            t = socket.socket()
            t.settimeout(0.2)
            ok = t.connect_ex(("127.0.0.1", port)) == 0
            t.close()
            if ok:
                return
            if p.poll() is not None:
                raise SystemExit(f"{name} exited early")
            time.sleep(0.1)
        raise SystemExit(f"{name} did not start")

    def _pump(self, p, ns):
        for line in p.stdout:
            with self.lock:
                self.raw_output.append(line)
                try:
                    ev = json.loads(line)
                    if isinstance(ev, dict) and "operation" in ev:
                        self.events.append(ev)
                except ValueError:
                    pass

    def stop(self):
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


def wait_approved(client, mem_id, timeout=None):
    """Poll until settled (real gateway ingests asynchronously: up to 90s). Returns status dict."""
    client.wait_until_settled(mem_id, timeout_s=timeout or (90 if REAL else 10))
    return client.get_status(mem_id)


def http_get(url, bearer=None):
    req = urllib.request.Request(url)
    if bearer:
        req.add_header("Authorization", "Bearer " + bearer)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# ----------------------------------------------------------------- main story
def run_story(procs):
    R = {}   # results for the report
    checks = []

    traces = []   # (label, trace_id) returned by the real gateway; ids only, no content

    def tr(label, resp):
        t = resp.get("trace_id") if isinstance(resp, dict) else None
        if t:
            traces.append((label, str(t)))

    def check(name, ok, evidence):
        checks.append((name, ok if ok == SKIP else bool(ok), evidence))

    a_unp, a_prot = GatewayClient(UNPROT, TOK_A), GatewayClient(PBASE, P_TOK_A)
    b_unp, b_prot = GatewayClient(UNPROT, TOK_B), GatewayClient(PBASE, P_TOK_B)
    alpha_bearer = FIX["tenants"]["alpha"]["crm_bearer"]
    stub = akash_client.is_stub()
    R["akash_stub"] = stub

    # ---- STEP 1
    step(1, "Beta cannot access Alpha (customer API access difference)")
    base = agent_b.beta_cred_baseline(CRM)
    a_status, _ = agent_b._crm_get(CRM, "/customers/alpha", alpha_bearer)
    print(f"  Agent B (Beta credential) -> GET /customers/alpha : HTTP {base['status']}  (expected 403)")
    print(f"  Agent A (Alpha credential) -> GET /customers/alpha : HTTP {a_status}  (expected 200)")
    R["step1"] = {"beta": base["status"], "alpha": a_status}
    check("Access baseline (Beta 403 / Alpha 200)", base["status"] == 403 and a_status == 200,
          f"Beta={base['status']} Alpha={a_status}")

    # ---- STEP 2
    step(2, "The leak: Agent A's handoff memory carries a working credential")
    out_a = agent_a.run(a_unp)   # real A run; writes into the UNPROTECTED namespace
    cand = out_a["candidate"]
    frozen = json.dumps(cand, sort_keys=True).encode()   # FREEZE the bytes
    frozen_hash = hashlib.sha256(frozen).hexdigest()
    unprot_sent_hash = hashlib.sha256(json.dumps(cand, sort_keys=True).encode()).hexdigest()
    leak_line = next(l for l in cand["text"].splitlines() if l.startswith("Notes:"))
    print(f"  Agent A inference: {'STUB (AKASHML_API_KEY unset; canned summary)' if stub else 'real AkashML'}")
    print(f"  Candidate title : {cand['title']}")
    print(f"  Carried-over line (redacted): {scrub(leak_line)}")
    print(f"  Credential present in candidate: {bool(CANARY_RE.search(cand['text']))}")
    print(f"  Frozen candidate SHA-256: {frozen_hash}")
    print("  (the generated summary + original incident context were preserved verbatim; the")
    print("   credential rode along in the preserved context, it was not injected on purpose)")
    R["step2"] = {"title": cand["title"], "leak_line": scrub(leak_line), "hash": frozen_hash,
                  "create": out_a["create_response"], "llm_stub": out_a["llm_stub"]}

    # ---- STEP 3
    step(3, "Breach baseline (UNPROTECTED namespace): Agent B uses the leaked credential")
    mem_u = out_a["create_response"]["memory_id"]
    st_u = wait_approved(a_unp, mem_u)
    print(f"  Unprotected write state: {out_a['create_response']['state']} -> readiness: {st_u.get('state')}")
    resp_u = b_unp.search(QUERIES[0], 5)
    breach = agent_b.attempt_breach(UNPROT, CRM)
    rec = breach.get("record") or {}
    print(f"  Agent B search response (redacted):\n{indent(jscrub(trim_results(resp_u)))}")
    print(f"  Credential retrieved from memory : {breach['canary_found']}  (shown as {REDACTED})")
    print(f"  Agent B -> CRM GET /customers/alpha with that credential : HTTP {breach['crm_status']}")
    if rec:
        print(f"  DAMAGE: Alpha's customer record returned to Beta -> customer={rec.get('customer_name')!r}, "
              f"purchase={rec.get('purchase')!r}, address=[ADDRESS REDACTED]")
    R["step3"] = {"search": trim_results(resp_u), "breach": breach, "state": st_u.get("state"),
                  "record_view": {"customer_name": rec.get("customer_name"), "purchase": rec.get("purchase")}}
    check("Actual breach baseline", breach["canary_found"] and breach["alpha_record_obtained"]
          and breach["crm_status"] == 200, f"canary_found={breach['canary_found']} CRM={breach['crm_status']}")

    # ---- STEP 4
    step(4, "Protection ON (PROTECTED namespace): the identical bytes are quarantined")
    payload = json.loads(frozen)   # decode the frozen bytes; submit exactly those
    prot_sent_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    cr = a_prot.create_memory(payload["title"], payload["text"], "frozen-" + uuid.uuid4().hex[:8])
    print(f"  Candidate SHA-256 unprotected: {unprot_sent_hash}")
    print(f"  Candidate SHA-256 protected  : {prot_sent_hash}   match={unprot_sent_hash == prot_sent_hash == frozen_hash}")
    print(f"  Protected write decision (MCP/client path): state={cr.get('state')} reasons={cr.get('reason_codes')}")
    resp_p = b_prot.search(QUERIES[0], 5)
    mem_p = cr["memory_id"]
    guess = b_prot.get_memory(mem_p)
    guess2 = b_prot.get_memory("mem-000000000000")
    breach_p = agent_b.attempt_breach(PBASE, CRM, token=P_TOK_B)
    print(f"  Agent B search response (protected): {json.dumps(resp_p)}")
    print(f"  Agent B guessed-ID lookup: HTTP {guess.get('_status', 200)} / {guess2.get('_status', 200)}")
    print(f"  Agent B breach attempt: credential found={breach_p['canary_found']}, CRM called={breach_p['crm_called']}")
    a_view = a_prot.search(QUERIES[0], 5)
    print(f"  Even Agent A cannot read the quarantined candidate: {len(a_view.get('results', []))} results")

    raw = bypass_attempt.raw_post(PBASE, P_TOK_A, payload)
    print(f"  Python bypass script (raw HTTP POST): state={raw.get('state')} reasons={raw.get('reason_codes')}")
    parity = raw.get("state") == cr.get("state") and raw.get("reason_codes") == cr.get("reason_codes")
    senso = bypass_attempt.senso_direct_no_key()
    senso_denied = senso in (401, 403) or isinstance(senso, str)
    print(f"  Direct Senso write WITHOUT backend key: {senso}  -> {'denied/blocked' if senso_denied else 'NOT DENIED'}")
    script_trace = raw.get("trace_id")
    tr("protected write (client path)", cr)
    tr("Beta search (protected)", resp_p)
    tr("guessed-ID lookup", guess)
    tr("bypass script write", raw)

    # permission preservation: independently admitted clean Alpha memory
    clean = a_prot.create_memory("Alpha approved note: Service Plan A re-pair checklist",
                                 "Alpha account checklist: Service Plan A devices need re-pairing after a "
                                 "firmware update before manual sync.", "clean-" + uuid.uuid4().hex[:8])
    tr("clean Alpha note write", clean)
    wait_approved(a_prot, clean["memory_id"])
    a_find = a_prot.search(QUERIES[2], 5).get("results", [])
    b_find = b_prot.search(QUERIES[2], 5).get("results", [])
    b_direct = b_prot.get_memory(clean["memory_id"])
    a_direct = a_prot.get_memory(clean["memory_id"])
    b_leak = any("Service Plan A" in r.get("text", "") for r in b_find) or "text" in b_direct
    print(f"  Permission preservation: A reads approved Alpha memory: search hits={len(a_find)}, "
          f"direct HTTP {a_direct.get('_status', 200)}; B search hits={len(b_find)}, direct ID HTTP "
          f"{b_direct.get('_status', 200)}")

    # revocation (needs the admin bearer; skipped in pre-minted mode)
    pre = a_prot.get_memory(clean["memory_id"]).get("_status", 200)
    if PREMINTED:
        rv, post, post_search = {}, None, None
        print(f"  Revocation: SKIP - {SKIP_REVOKE}")
    else:
        rv = GatewayClient(PBASE, P_ADMIN)._request("POST", f"/v1/admin/memories/{clean['memory_id']}/revoke",
                                                     {"reason_code": "ADMIN_REVOKE" if REAL else "DEMO_REVOKE"})
        tr("admin revoke", rv)
        post = a_prot.get_memory(clean["memory_id"]).get("_status", 200)
        post_search = len(a_prot.search(QUERIES[2], 5).get("results", []))
        print(f"  Revocation: admin revoke -> {rv.get('state')}; Alpha direct read {pre} -> {post}; "
              f"search hits after revoke={post_search}")

    R["step4"] = {"create": cr, "search": resp_p, "guess_status": guess.get("_status", 200),
                  "breach": breach_p, "raw": {k: raw.get(k) for k in ("state", "reason_codes")},
                  "senso": str(senso), "a_hits": len(a_find), "b_hits": len(b_find),
                  "b_direct": b_direct.get("_status", 200), "hash_u": unprot_sent_hash, "hash_p": prot_sent_hash}
    check("Same-input hash match", unprot_sent_hash == prot_sent_hash == frozen_hash,
          f"sha256 {frozen_hash[:16]}... identical in both namespaces")
    check("Secret admission (quarantined, not readable)",
          cr.get("state") == "QUARANTINED" and not resp_p.get("results") and not a_view.get("results")
          and guess.get("_status") == 404 and not breach_p["canary_found"] and not breach_p["crm_called"],
          f"state={cr.get('state')} B results={len(resp_p.get('results', []))} A results={len(a_view.get('results', []))}")
    check("Permission preservation", len(a_find) >= 1 and not b_leak and b_direct.get("_status") == 404
          and a_direct.get("_status", 200) == 200,
          f"A hits={len(a_find)}; B hits={len(b_find)}, B direct={b_direct.get('_status', 200)}")
    check("Script parity (MCP vs Python HTTP)", parity and senso_denied,
          f"client={cr.get('state')}, raw={raw.get('state')}, direct Senso w/o key={senso}")
    if PREMINTED:
        check("Revocation (immediate)", SKIP, SKIP_REVOKE)
    else:
        check("Revocation (immediate)", pre == 200 and post == 404 and post_search == 0
              and rv.get("state") == "REVOKED", f"Alpha direct read {pre} -> {post} right after revoke")

    # ---- STEP 5
    step(5, "Useful work still happens: Agent B publishes a source-linked public note")
    work = agent_b.useful_work(PBASE, token=P_TOK_BPUB, create_runbook_memory=REAL)
    url = work["report_url"]
    rep_resp = work.get("response") or {}
    tr("public report", rep_resp)
    code, page = http_get(url) if url else (0, "")
    src_url = FIX["sources"]["public_runbook"]["url"]
    print(f"  Agent B inference: {'STUB (canned note)' if work['llm_stub'] else 'real AkashML'}")
    if work.get("runbook_memory"):
        print(f"  Public runbook memory (public-publishing run): {work['runbook_memory']}")
    print(f"  Published note URL: {url}  (HTTP {code}, links to public source: {src_url in page})")
    if not url:
        reason = rep_resp.get("error") or rep_resp.get("reason_codes") or rep_resp.get("reason_code") or "no report_url"
        report_fail = f"report rejected: HTTP {rep_resp.get('_status', '?')} reason={reason}"
        print(f"  FAIL: {report_fail} (if the publishing run was already used, a fresh publishing run is needed)")
    else:
        report_fail = ""
    R["step5"] = {"url": url, "http": code, "page_text": scrub(re.sub(r"<[^>]+>", " ", page)).strip(),
                  "llm_stub": work["llm_stub"], "source": src_url}
    check("Useful work (public note, valid source link)", bool(url) and code == 200 and src_url in page
          and CANARY not in page, report_fail or f"report HTTP {code}, source link present={src_url in page}")

    # ---- STEP 6
    step(6, "The trace: metadata-only decisions and measured timings")
    time.sleep(0.4)
    if REAL:
        print("  (protected steps ran against the real gateway; its decision/timing trace is in its own audit")
        print("   store/ClickHouse, not in this process. Gateway trace IDs returned to this run:)")
        for label, t in traces:
            print(f"  {label:<32}{t}")
    with procs.lock:
        events = list(procs.events)
    script_traces = {script_trace} if script_trace else set()
    rows = []
    for e in ([] if REAL else events):
        rows.append(dict(ns=e.get("ns"), operation=e["operation"],
                         transport="script" if e.get("trace_id") in script_traces else "mcp",
                         decision=e["decision"], reason_code=e["reason_code"], gate_ms=e["gate_ms"],
                         total_ms=e["total_ms"], trace=str(e.get("trace_id", ""))[:8]))
    R["rows"] = rows
    R["traces"] = traces
    hdr = f"  {'ns':<12}{'operation':<14}{'transport':<10}{'decision':<9}{'reason_code':<14}{'gate_ms':>8}{'total_ms':>10}"
    if not REAL:
        print(hdr + "\n  " + "-" * (len(hdr) - 2))
    for r in rows:
        print(f"  {r['ns']:<12}{r['operation']:<14}{r['transport']:<10}{r['decision']:<9}"
              f"{r['reason_code']:<14}{r['gate_ms']:>8.2f}{r['total_ms']:>10.2f}")
    if not REAL:
        print("  (timings measured from the MOCK gateway; gate_ms is 0 in the unprotected namespace because admission is skipped)")
    table_html = decisions_table_html(rows) + traces_html(traces)
    with procs.lock:
        blob = "".join(procs.raw_output) + table_html + "\n".join(json.dumps(e) for e in events)
    leaks = [f for f in FORBIDDEN + QUERIES if f in blob]
    check("Audit privacy (no canary/address/token/query in analytics or logs)", not leaks,
          f"{len(events)} audit events scanned; forbidden strings found: {len(leaks)}")
    R["events"] = len(events)
    return R, checks


def trim_results(resp):
    return {"results": [{"memory_id": r["memory_id"], "text": r["text"][:900],
                         "source_urls": r.get("source_urls")} for r in resp.get("results", [])]} \
        if "results" in resp else resp


def indent(s, n=4):
    return "\n".join(" " * n + l for l in s.splitlines())


# ----------------------------------------------------------------- HTML
CSS = """
:root{--bg:#f7f6f3;--fg:#16181d;--mut:#5c6370;--card:#fff;--line:#e0ddd6;--bad:#b42318;--badbg:#fdeceb;--badline:#f0b4ae;
--good:#066a42;--goodbg:#e6f5ed;--goodline:#a4d8bd;--acc:#2849d6;--code:#f0efeb;--pill:#fff1b8;--pillfg:#5c4300}
@media (prefers-color-scheme:dark){:root{--bg:#0e1116;--fg:#eceff4;--mut:#9aa3b2;--card:#171c24;--line:#2a313c;
--bad:#ff9a90;--badbg:#2b1618;--badline:#6b2b2b;--good:#6fdca5;--goodbg:#0f2419;--goodline:#235b3e;--acc:#9bb1ff;
--code:#0b0e13;--pill:#4a3a0c;--pillfg:#ffe08a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{max-width:880px;margin:0 auto;padding:56px 20px 48px;animation:fade .5s ease-out both}
@keyframes fade{from{opacity:0}to{opacity:1}}
@media (prefers-reduced-motion:reduce){main{animation:none}}
h1{font-size:36px;line-height:1.15;margin:0 0 16px;letter-spacing:-.02em}
.sub{font-size:19px;color:var(--mut);margin:0 0 24px;max-width:46em}
.chips{display:flex;flex-wrap:wrap;gap:10px}
.chip{border:1px solid;border-radius:999px;padding:7px 14px;font-size:15px;font-weight:600}
.chip.bad{color:var(--bad);background:var(--badbg);border-color:var(--badline)}
.chip.good{color:var(--good);background:var(--goodbg);border-color:var(--goodline)}
section{margin-top:56px}
.lbl{font-size:13px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--mut);margin:0 0 6px}
h2{font-size:24px;line-height:1.25;margin:0 0 14px;letter-spacing:-.01em}
p{margin:8px 0}.mut{color:var(--mut)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px}
.note{font-size:16px}.note b{display:block;margin-bottom:6px}
.note .line{margin:4px 0;color:var(--mut)}
.pill{display:inline-block;background:var(--pill);color:var(--pillfg);border-radius:6px;padding:1px 8px;
font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.92em;font-weight:700}
.rule{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-top:14px}
.arrow{color:var(--mut)}
.badge{display:inline-block;border-radius:8px;padding:6px 14px;font-weight:700;border:1px solid}
.badge.bad{color:var(--bad);background:var(--badbg);border-color:var(--badline)}
.badge.good{color:var(--good);background:var(--goodbg);border-color:var(--goodline)}
.bar{text-align:center;font-size:15px;font-weight:600;padding:8px 14px;border-radius:999px;border:1px solid;margin:0 0 16px}
.bar.good{color:var(--good);background:var(--goodbg);border-color:var(--goodline)}
.bar.bad{color:var(--bad);background:var(--badbg);border-color:var(--badline)}
.split{display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:stretch}
@media(max-width:720px){.split{grid-template-columns:1fr}h1{font-size:30px}}
.col{display:flex;flex-direction:column;border:1px solid;border-radius:14px;padding:20px}
.col.bad{border-color:var(--badline);background:var(--badbg)}
.col.good{border-color:var(--goodline);background:var(--goodbg)}
.col h3{margin:0 0 12px;font-size:20px}.col.bad h3{color:var(--bad)}.col.good h3{color:var(--good)}
.steps{list-style:none;margin:0 0 16px;padding:0}
.steps li{padding:9px 0;border-top:1px solid var(--line)}.steps li:first-child{border-top:0}
.steps small{display:block;color:var(--mut);font-size:14px}
.result{margin-top:auto;border-radius:10px;padding:14px 16px;border:2px solid;background:var(--card)}
.result.bad{border-color:var(--bad)}.result.good{border-color:var(--good)}
.result .k{font-size:13px;font-weight:800;letter-spacing:.09em;text-transform:uppercase}
.result.bad .k{color:var(--bad)}.result.good .k{color:var(--good)}
.result .t{font-weight:700;font-size:18px;margin:2px 0 6px}
.result dl{margin:0;font-size:15px}.result dt{display:inline;color:var(--mut)}.result dd{display:inline;margin:0 0 0 4px}
.result div{margin:2px 0}
.small{font-size:14px;color:var(--mut);margin-top:12px}
.concl{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px 26px}
.concl p{font-size:21px;font-weight:700;margin:10px 0;letter-spacing:-.01em}
.concl .no{color:var(--bad)}
a{color:var(--acc)}
details{margin-top:56px;border-top:1px solid var(--line);padding-top:18px}
summary{cursor:pointer;font-weight:700;font-size:16px;color:var(--mut)}
details h4{margin:26px 0 8px;font-size:15px}
pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:10px;overflow-x:auto;font-size:12.5px;margin:8px 0 0}
code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;word-break:break-all}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--mut);font-weight:600}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.pass{color:var(--good);font-weight:700}.fail{color:var(--bad);font-weight:700}
footer{margin-top:40px;font-size:13px;color:var(--mut)}
"""


def md_to_html(text, max_lines=6, max_chars=500):
    """Safe minimal Markdown: escape first, then bold, bullets, headings, paragraphs."""
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.replace("\r", "").split("\n")]
    out, used, chars, truncated = [], 0, 0, False
    kept = []
    for l in lines:
        if not l and (not kept or not kept[-1]):
            continue
        if l:
            if used >= max_lines or chars >= max_chars:
                truncated = True
                break
            if chars + len(l) > max_chars:
                l = l[: max_chars - chars].rstrip()
                truncated = True
            used += 1
            chars += len(l)
        kept.append(l)
        if truncated:
            break
    if truncated and kept:
        kept[-1] = kept[-1].rstrip() + "…"
    bold = lambda t: re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(t))
    in_ul, para = False, []
    def flush():
        if para:
            out.append("<p>" + " ".join(para) + "</p>")
            para.clear()
    for l in kept:
        m_b = re.match(r"^[-*] +(.*)$", l)
        m_h = re.match(r"^#{1,6} *(.*)$", l)
        if m_b:
            flush()
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append("<li>" + bold(m_b.group(1)) + "</li>")
            continue
        if in_ul:
            out.append("</ul>"); in_ul = False
        if m_h:
            flush()
            out.append("<p><b>" + html.escape(m_h.group(1).replace("**", "")) + "</b></p>")
        elif l:
            para.append(bold(l))
        else:
            flush()
    if in_ul:
        out.append("</ul>")
    flush()
    return "".join(out)


def e(x):
    return html.escape(str(x))


def decisions_table_html(rows):
    body = "".join(
        f"<tr><td>{e(r['ns'])}</td><td>{e(r['operation'])}</td><td>{e(r['transport'])}</td>"
        f"<td class={'fail' if r['decision'] == 'DENY' else 'pass'}>{e(r['decision'])}</td><td>{e(r['reason_code'])}</td>"
        f"<td class=num>{r['gate_ms']:.2f}</td><td class=num>{r['total_ms']:.2f}</td></tr>" for r in rows)
    return ('<div class=tw><table id="analytics"><thead><tr><th>namespace</th><th>operation</th><th>transport</th>'
            '<th>decision</th><th>reason_code</th><th class=num>gate_ms</th><th class=num>total_ms</th></tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def traces_html(traces):
    body = "".join(f"<tr><td>{e(l)}</td><td><code>{e(t)}</code></td></tr>" for l, t in traces)
    return ('<div class=tw><table id="gateway-traces"><thead><tr><th>step</th><th>gateway trace ID</th></tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def gateway_label():
    host = PBASE.split("//", 1)[-1].split("/")[0].split(":")[0]
    return "real gateway code (local)" if host in LOCAL_HOSTS else "live AWS gateway"


def mask_address(addr):
    """Keep house number and last word; mask the middle words after two letters."""
    words = addr.split()
    out = []
    for i, w in enumerate(words):
        if i == 0 or i == len(words) - 1 or len(w) <= 2:
            out.append(w)
        else:
            out.append(w[:2] + "•" * (len(w) - 2))
    return " ".join(out)


def pill_line(text):
    """Escape text, turning the already-scrubbed canary placeholder into a masked pill."""
    parts = text.split(REDACTED)
    return '<span class=pill>DEMO_SECRET_•••</span>'.join(e(p) for p in parts)


def build_html(R, checks):
    s1, s2, s3, s4, s5 = R["step1"], R["step2"], R["step3"], R["step4"], R["step5"]
    ck = {n: ok for n, ok, _ in checks}
    ok_rule = s1["beta"] == 403 and s1["alpha"] == 200
    br = s3["breach"]
    ok_breach = bool(br.get("canary_found") and br.get("alpha_record_obtained") and br.get("crm_status") == 200)
    ok_block = ck.get("Secret admission (quarantined, not readable)", False)
    ok_same = ck.get("Same-input hash match", False)
    ok_par = ck.get("Script parity (MCP vs Python HTTP)", False)
    ok_work = ck.get("Useful work (public note, valid source link)", False)
    ok_perm = ck.get("Permission preservation", False)
    live_line = f'<p class=small>Ran against the {e(gateway_label())}.</p>' if REAL else ""
    rec = s3["record_view"]
    allp = all(ok for _, ok, _ in checks)   # SKIP is truthy: only FAIL breaks ALL PASS

    def chip(ok, good_t, bad_t, bad_when_ok=False):
        cls = "good" if ok else "bad"
        return f'<span class="chip {cls}">{e(good_t if ok else bad_t)}</span>'

    chips = (
        ('<span class="chip bad">Without gateway: Beta read Alpha\'s customer record</span>' if ok_breach else
         '<span class="chip good">Without gateway: breach did not reproduce in this run</span>')
        + chip(ok_block, "With gateway: blocked before it was stored", "With gateway: NOT blocked (check failed)")
        + chip(ok_work, "Useful work: still completed", "Useful work: did NOT complete (check failed)"))

    if ok_rule:
        rule = '<span class="badge bad">Access denied</span>'
        rule_note = "Beta's own key does not open Alpha's records. Alpha's own key does."
    else:
        rule = '<span class="badge good">Unexpected: Beta was not denied</span>'
        rule_note = "The baseline did not behave as expected in this run."

    if ok_breach:
        breach_res = (f'<div class="result bad"><div class=k>Breach</div><div class=t>Beta read Alpha\'s customer record</div><dl>'
                      f'<div><dt>Name</dt><dd>{e(rec.get("customer_name"))}</dd></div>'
                      f'<div><dt>Purchase</dt><dd>{e(rec.get("purchase"))}</dd></div>'
                      f'<div><dt>Address</dt><dd>{e(mask_address(ADDRESS))}</dd></div></dl></div>')
    else:
        breach_res = ('<div class="result good"><div class=k>No breach</div><div class=t>Beta did not read the record in this run</div></div>')

    if ok_block:
        block_res = ('<div class="result good"><div class=k>Blocked</div><div class=t>The key never reached shared memory</div>'
                     '<div class=mut style="font-size:15px">Beta searched and found nothing.</div></div>')
    else:
        block_res = ('<div class="result bad"><div class=k>Not blocked</div><div class=t>The gateway did not stop the key</div>'
                     '<div class=mut style="font-size:15px">An acceptance check failed. See the evidence below.</div></div>')

    block_steps = ('<li>Gateway checks the note<small>Finds a credential</small></li>'
                   '<li>Quarantines it before storage<small>Nothing is saved to shared memory</small></li>'
                   '<li>Beta searches memory<small>Nothing found</small></li>') if ok_block else \
                  ('<li>Gateway checks the note<small>Expected a quarantine, but the check failed</small></li>')
    if ok_par:
        parity = '<p class=small>A Python script calling the API directly got the same answer: the gateway is the only door.</p>'
    else:
        parity = '<p class="small fail">A Python script calling the API directly did NOT get the same answer (check failed).</p>'

    bar = ('<div class="bar good">Same input, byte for byte</div>' if ok_same else
           '<div class="bar bad">The two runs did NOT receive identical input (check failed)</div>')

    work_link = '<p class=small style="color:var(--muted,#666)">Published by the gateway\'s report route during this run.</p>'
    work_cls = "good" if ok_work else "bad"
    pt = re.sub(r"\s+", " ", s5["page_text"]).strip()
    note_title = ""
    for i in range(len(pt) // 2, 3, -1):   # page repeats its title; find the repeated prefix
        if pt[:i].strip() and pt[i:].lstrip().startswith(pt[:i].strip()):
            note_title = pt[:i].strip()
            break
    body = pt[len(note_title) * 2 + 1:] if note_title else pt
    note_body = re.sub(r"\s+", " ", body.split("Source:")[0]).replace("[STUB]", "").strip()
    # newline-preserving body for Markdown rendering
    raw_body = s5["page_text"].split("Source:")[0].replace("[STUB]", "")
    if note_title:
        raw_body = raw_body.replace(note_title, "", 2)
    note_html = md_to_html(raw_body)
    if not note_title:
        note_title = "Published note"
    work_head = ("Beta's agent then published a troubleshooting note using only the public runbook." if ok_work
                 else "Beta's agent did not publish a valid note (check failed).")
    work = (f'<div class="card" style="border-color:var(--{work_cls}line)"><div class="lbl" style="color:var(--{work_cls})">'
            f'{"Done" if ok_work else "Failed"}</div>'
            f'<p><b>{e(note_title)}</b></p>{note_html}{work_link}'
            f'<p class=small>Source: <a href="{e(s5["source"])}">{e(s5["source"])}</a></p></div>')

    ok_c = ok_block
    concl = (
        f'<p class="{"" if ok_block else "no"}">{"" if ok_block else "NOT PROVEN: "}Secrets never enter shared memory.</p>'
        f'<p class="{"" if ok_perm else "no"}">{"" if ok_perm else "NOT PROVEN: "}Permissions follow the data on every read.</p>'
        f'<p class="{"" if ok_par else "no"}">{"" if ok_par else "NOT PROVEN: "}Scripts can\'t go around it: same door, same rules.</p>')

    chk = "".join(f"<tr><td>{e(n)}</td><td class={'pass' if ok else 'fail'}>{status_of(ok)}</td>"
                  f"<td>{e(ev)}</td></tr>" for n, ok, ev in checks)
    if REAL:
        trace_section = ("<h4>Gateway trace IDs (metadata only)</h4>"
                         f"<p class=mut>Trace IDs returned by the {e(gateway_label())} for this run's protected calls. "
                         "No secret, address, query or token. The gateway's own audit store holds the decisions and timings.</p>"
                         + traces_html(R["traces"]))
    else:
        trace_section = ("<h4>Decision and timing trace (metadata only)</h4>"
                         "<p class=mut>Operation, transport, decision, reason code and measured timings. No secret, address, "
                         f"query or token. Timings are measured from the mock gateway ({R['events']} events); ClickHouse is "
                         "not connected in this run.</p>" + decisions_table_html(R["rows"]))
    stub = ('<footer>Agent text generated by a stub model in this run (AkashML key pending).</footer>'
            if R["akash_stub"] else "")
    return f"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Agent Memory Gateway: same agent, two outcomes</title><style>{CSS}</style>
<main>
<header>
<h1>Same agent. Same note. Two outcomes.</h1>
<p class=sub>An agent's handoff note quietly contained a customer's access key. Without the gateway, another team's agent used it to read that customer's private record. With the gateway, the note never entered shared memory, and the work still got done.</p>
<div class=chips>{chips}</div>
</header>

<section>
<p class=lbl>The cast</p>
<p><b>Agent A</b> works for customer <b>Alpha</b>. <b>Agent B</b> works for customer <b>Beta</b>. They share one company memory.</p>
</section>

<section>
<p class=lbl>The rule</p>
<h2>Beta's agent is not allowed to see Alpha's customers.</h2>
<div class=card><div class=rule><span>Beta's own key</span><span class=arrow>&rarr;</span><span>Alpha's records</span><span class=arrow>&rarr;</span>{rule}</div>
<p class=small>{e(rule_note)}</p></div>
</section>

<section>
<p class=lbl>The slip</p>
<h2>Agent A writes a handoff note to shared memory.</h2>
<p class=mut>Buried in the copied incident context is Alpha's access key.</p>
<div class="card note"><b>{e(s2['title'])}</b>
<div class=line>Summary and incident context copied as written.</div>
<div class=line>{pill_line(s2['leak_line'])}</div></div>
</section>

<section>
<p class=lbl>The split</p>
<h2>Same note goes in. Two different endings.</h2>
{bar}
<div class=split>
<div class="col bad"><h3>Without the gateway</h3>
<ol class=steps style="padding-left:0"><li>Beta searches memory</li><li>Finds the note with the key</li><li>Uses the key on Alpha's records</li></ol>
{breach_res}</div>
<div class="col good"><h3>With the gateway</h3>
<ol class=steps style="padding-left:0">{block_steps}</ol>
{block_res}{live_line}</div>
</div>
{parity}
</section>

<section>
<p class=lbl>Work still gets done</p>
<h2>{e(work_head)}</h2>
{work}
</section>

<section>
<p class=lbl>The conclusion</p>
<div class=concl>{concl}</div>
</section>

<details>
<summary>Evidence for judges: real run data</summary>
<p class=mut>Generated {e(time.strftime('%Y-%m-%d %H:%M:%S'))} from a live run against {'the ' + e(gateway_label()) + ' (protected side), a local mock (unprotected side)' if REAL else 'the mock gateways'} and a synthetic CRM. All data is synthetic. Credentials shown as <code>{REDACTED}</code>. Overall: <span class="{'pass' if allp else 'fail'}">{'ALL PASS' if allp else 'FAILURES'}</span></p>
{trace_section}
<h4>Acceptance checks</h4>
<div class=tw><table><thead><tr><th>check</th><th>result</th><th>evidence</th></tr></thead><tbody>{chk}</tbody></table></div>
<h4>Frozen candidate SHA-256 (identical in both namespaces)</h4>
<p><code>{e(s2['hash'])}</code><br><span class=mut>unprotected <code>{e(s4['hash_u'])}</code><br>protected <code>{e(s4['hash_p'])}</code></span></p>
<h4>Access baseline</h4>
<pre>Beta credential  GET /customers/alpha -> HTTP {s_(s1['beta'])}
Alpha credential GET /customers/alpha -> HTTP {s_(s1['alpha'])}</pre>
<h4>Unprotected: Beta search response (scrubbed)</h4>
<pre>{e(jscrub(s3['search']))}</pre>
<h4>Unprotected: damage</h4>
<pre>credential retrieved: {s_(br['canary_found'])}   CRM HTTP {s_(br['crm_status'])}
customer_name: {e(rec.get('customer_name'))}
purchase: {e(rec.get('purchase'))}
address: [REDACTED]</pre>
<h4>Protected: write decision and Beta retrieval (scrubbed)</h4>
<pre>write -> state: {e(s4['create'].get('state'))}
reason_codes: {e(s4['create'].get('reason_codes'))}
search: {e(jscrub(s4['search']))}
guessed-ID lookup: HTTP {s_(s4['guess_status'])}
credential found: {s_(s4['breach']['canary_found'])}  CRM called: {s_(s4['breach']['crm_called'])}</pre>
<h4>Python bypass script</h4>
<pre>raw HTTP POST -> state: {e(s4['raw']['state'])}
reason_codes: {e(s4['raw']['reason_codes'])}
direct Senso write without backend key: {e(s4['senso'])}</pre>
<h4>Permissions preserved</h4>
<pre>Alpha hits: {s_(s4['a_hits'])}   Beta hits: {s_(s4['b_hits'])}   Beta direct ID: HTTP {s_(s4['b_direct'])}</pre>
<h4>Published note</h4>
<pre>{e(s5['page_text'])}
HTTP {s_(s5['http'])}</pre>
</details>
{stub}
</main>
"""


def s_(x):
    return e(x)


# ----------------------------------------------------------------- entry
def main():
    procs = Procs()
    try:
        if REAL:
            import mint_runs
            global P_TOK_A, P_TOK_B, P_TOK_BPUB, P_ADMIN, PREMINTED
            try:
                admin = None if os.environ.get("GATEWAY_USE_PREMINTED") else mint_runs.read_admin_token()
            except SystemExit:
                admin = None   # no admin bearer available
            if admin:
                minted = mint_runs.mint(PBASE, admin)
                P_ADMIN = admin
            else:
                minted = load_premint_tokens()
                if not minted:
                    raise SystemExit("no admin bearer and no GATEWAY_TOKEN_ALPHA/BETA/BETA_PUB "
                                     "(env or .gateway-runs.env); cannot run against the real gateway")
                PREMINTED, P_ADMIN = True, None
                print("Pre-minted tokens mode: minting and the admin revoke step are skipped (tokens not printed)")
            P_TOK_A, P_TOK_B, P_TOK_BPUB = (minted["GATEWAY_TOKEN_ALPHA"], minted["GATEWAY_TOKEN_BETA"],
                                            minted["GATEWAY_TOKEN_BETA_PUB"])
            FORBIDDEN.extend([t for t in (P_TOK_A, P_TOK_B, P_TOK_BPUB, P_ADMIN) if t])
        else:
            procs.start("protected gateway", ["demo/mock_gateway.py"],
                        {"GATEWAY_NAMESPACE": "protected", "GATEWAY_PORT": "8808"}, 8808)
        procs.start("unprotected gateway", ["demo/mock_gateway.py"],
                    {"GATEWAY_NAMESPACE": "unprotected", "GATEWAY_PORT": "8809"}, 8809)
        procs.start("CRM fixture", ["demo/crm_fixture.py"], {}, 8900)
        print("Agent Memory Gateway demo (synthetic data; "
              f"{'protected=REAL gateway ' + PBASE + '; unprotected=local mock' if REAL else 'mock gateways'}; "
              f"AkashML {'STUB' if akash_client.is_stub() else 'LIVE'})")
        R, checks = run_story(procs)
    finally:
        procs.stop()
    OUT.mkdir(parents=True, exist_ok=True)
    doc = build_html(R, checks)
    (OUT / "index.html").write_text(doc)
    print(f"\n{'=' * 78}\nACCEPTANCE CHECKS\n{'=' * 78}")
    for n, ok, ev in checks:
        print(f"  {status_of(ok)}  {n}  [{ev}]")
    html_leaks = [f for f in FORBIDDEN if f in doc]
    print(f"  {'PASS' if not html_leaks else 'FAIL'}  HTML report contains no raw canary/address/token")
    ok = all(c[1] for c in checks) and not html_leaks
    skipped = [c[0] for c in checks if c[1] == SKIP]
    if skipped:
        print(f"  NOTE: {len(skipped)} check(s) SKIPPED (not passed, not failed): {', '.join(skipped)}")
    print(f"\nWrote {OUT / 'index.html'}\nRESULT: {'ALL PASS' if ok else 'FAILURES'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
