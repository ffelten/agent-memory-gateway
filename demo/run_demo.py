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


def wait_approved(client, mem_id, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get_status(mem_id)
        if st.get("state") in ("APPROVED", "QUARANTINED", "REVOKED"):
            return st
        time.sleep(0.1)
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

    def check(name, ok, evidence):
        checks.append((name, bool(ok), evidence))

    a_unp, a_prot = GatewayClient(UNPROT, TOK_A), GatewayClient(PROT, TOK_A)
    b_unp, b_prot = GatewayClient(UNPROT, TOK_B), GatewayClient(PROT, TOK_B)
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
    breach_p = agent_b.attempt_breach(PROT, CRM)
    print(f"  Agent B search response (protected): {json.dumps(resp_p)}")
    print(f"  Agent B guessed-ID lookup: HTTP {guess.get('_status', 200)} / {guess2.get('_status', 200)}")
    print(f"  Agent B breach attempt: credential found={breach_p['canary_found']}, CRM called={breach_p['crm_called']}")
    a_view = a_prot.search(QUERIES[0], 5)
    print(f"  Even Agent A cannot read the quarantined candidate: {len(a_view.get('results', []))} results")

    raw = bypass_attempt.raw_post(PROT, TOK_A, payload)
    print(f"  Python bypass script (raw HTTP POST): state={raw.get('state')} reasons={raw.get('reason_codes')}")
    parity = raw.get("state") == cr.get("state") and raw.get("reason_codes") == cr.get("reason_codes")
    senso = bypass_attempt.senso_direct_no_key()
    senso_denied = senso in (401, 403) or isinstance(senso, str)
    print(f"  Direct Senso write WITHOUT backend key: {senso}  -> {'denied/blocked' if senso_denied else 'NOT DENIED'}")
    script_trace = raw.get("trace_id")

    # permission preservation: independently admitted clean Alpha memory
    clean = a_prot.create_memory("Alpha approved note: Service Plan A re-pair checklist",
                                 "Alpha account checklist: Service Plan A devices need re-pairing after a "
                                 "firmware update before manual sync.", "clean-" + uuid.uuid4().hex[:8])
    wait_approved(a_prot, clean["memory_id"])
    a_find = a_prot.search(QUERIES[2], 5).get("results", [])
    b_find = b_prot.search(QUERIES[2], 5).get("results", [])
    b_direct = b_prot.get_memory(clean["memory_id"])
    a_direct = a_prot.get_memory(clean["memory_id"])
    b_leak = any("Service Plan A" in r.get("text", "") for r in b_find) or "text" in b_direct
    print(f"  Permission preservation: A reads approved Alpha memory: search hits={len(a_find)}, "
          f"direct HTTP {a_direct.get('_status', 200)}; B search hits={len(b_find)}, direct ID HTTP "
          f"{b_direct.get('_status', 200)}")

    # revocation
    pre = a_prot.get_memory(clean["memory_id"]).get("_status", 200)
    rv = GatewayClient(PROT, TOK_ADMIN)._request("POST", f"/v1/admin/memories/{clean['memory_id']}/revoke",
                                                 {"reason_code": "DEMO_REVOKE"})
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
    check("Revocation (immediate)", pre == 200 and post == 404 and post_search == 0 and rv.get("state") == "REVOKED",
          f"Alpha direct read {pre} -> {post} right after revoke")

    # ---- STEP 5
    step(5, "Useful work still happens: Agent B publishes a source-linked public note")
    work = agent_b.useful_work(PROT)
    url = work["report_url"]
    code, page = http_get(url) if url else (0, "")
    src_url = FIX["sources"]["public_runbook"]["url"]
    print(f"  Agent B inference: {'STUB (canned note)' if work['llm_stub'] else 'real AkashML'}")
    print(f"  Published note URL: {url}  (HTTP {code}, links to public source: {src_url in page})")
    R["step5"] = {"url": url, "http": code, "page_text": scrub(re.sub(r"<[^>]+>", " ", page)).strip(),
                  "llm_stub": work["llm_stub"], "source": src_url}
    check("Useful work (public note, valid source link)", bool(url) and code == 200 and src_url in page
          and CANARY not in page, f"report HTTP {code}, source link present={src_url in page}")

    # ---- STEP 6
    step(6, "The trace: metadata-only decisions and measured timings")
    time.sleep(0.4)
    with procs.lock:
        events = list(procs.events)
    script_traces = {script_trace} if script_trace else set()
    rows = []
    for e in events:
        rows.append(dict(ns=e.get("ns"), operation=e["operation"],
                         transport="script" if e.get("trace_id") in script_traces else "mcp",
                         decision=e["decision"], reason_code=e["reason_code"], gate_ms=e["gate_ms"],
                         total_ms=e["total_ms"], trace=str(e.get("trace_id", ""))[:8]))
    R["rows"] = rows
    hdr = f"  {'ns':<12}{'operation':<14}{'transport':<10}{'decision':<9}{'reason_code':<14}{'gate_ms':>8}{'total_ms':>10}"
    print(hdr + "\n  " + "-" * (len(hdr) - 2))
    for r in rows:
        print(f"  {r['ns']:<12}{r['operation']:<14}{r['transport']:<10}{r['decision']:<9}"
              f"{r['reason_code']:<14}{r['gate_ms']:>8.2f}{r['total_ms']:>10.2f}")
    print("  (timings measured from the MOCK gateway; gate_ms is 0 in the unprotected namespace because admission is skipped)")
    table_html = decisions_table_html(rows)
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
:root{--bg:#f6f7f9;--fg:#14171c;--mut:#5d6674;--card:#fff;--line:#dde1e7;--bad:#b42318;--badbg:#fdecea;
--good:#067647;--goodbg:#e7f6ee;--acc:#2849d6;--code:#f0f2f5}
@media (prefers-color-scheme:dark){:root{--bg:#0e1116;--fg:#e8ebf0;--mut:#98a1b0;--card:#161b22;--line:#2a313c;
--bad:#ff8a80;--badbg:#2b1618;--good:#5fd39a;--goodbg:#10241b;--acc:#8aa4ff;--code:#0b0e13}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1080px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:28px;margin:0 0 6px;letter-spacing:-.02em}.pitch{color:var(--mut);font-size:17px;margin:0 0 28px}
h2{font-size:18px;margin:36px 0 12px;display:flex;gap:10px;align-items:center}
.n{background:var(--acc);color:#fff;border-radius:50%;width:26px;height:26px;display:inline-flex;
align-items:center;justify-content:center;font-size:13px;flex:none}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:760px){.grid{grid-template-columns:1fr}}
.bad{border-color:var(--bad);background:var(--badbg)}.good{border-color:var(--good);background:var(--goodbg)}
.tag{font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase}.bad .tag{color:var(--bad)}.good .tag{color:var(--good)}
pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:10px;overflow-x:auto;font-size:12.5px;margin:8px 0 0}
p{margin:6px 0}.mut{color:var(--mut)}.stub{font-size:12px;border:1px dashed var(--mut);border-radius:6px;padding:1px 6px;color:var(--mut)}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--mut);font-weight:600}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.pass{color:var(--good);font-weight:700}.fail{color:var(--bad);font-weight:700}
a{color:var(--acc)}code{font-family:ui-monospace,Menlo,monospace;font-size:12.5px}
"""


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


def build_html(R, checks):
    s2, s3, s4, s5 = R["step2"], R["step3"], R["step4"], R["step5"]
    stubnote = ('<span class=stub>AkashML = STUB (no API key): canned text, not real inference</span>'
                if R["akash_stub"] else "")
    crm_rec = s3["record_view"]
    chk = "".join(f"<tr><td>{e(n)}</td><td class={'pass' if ok else 'fail'}>{'PASS' if ok else 'FAIL'}</td>"
                  f"<td class=mut>{e(ev)}</td></tr>" for n, ok, ev in checks)
    allp = all(ok for _, ok, _ in checks)
    return f"""<title>Agent Memory Gateway: before / after</title><style>{CSS}</style>
<main>
<h1>Agent Memory Gateway</h1>
<p class=pitch>Agent memory leaks credentials across customers. The gateway quarantines the write, keeps permissions on every read, and the agents still finish useful work. {stubnote}</p>
<p class=mut>Generated {e(time.strftime('%Y-%m-%d %H:%M:%S'))} from a live run against the <b>mock</b> gateways (protected :8808, unprotected :8809) and a synthetic CRM. All data is synthetic. Credentials shown as <code>{REDACTED}</code>.</p>

<h2><span class=n>1</span>Beta cannot access Alpha</h2>
<div class=grid>
<div class="card bad"><span class=tag>Beta credential</span><p>GET /customers/alpha</p><pre>HTTP {s_(R['step1']['beta'])}</pre></div>
<div class="card good"><span class=tag>Alpha credential</span><p>GET /customers/alpha</p><pre>HTTP {s_(R['step1']['alpha'])}</pre></div>
</div>

<h2><span class=n>2</span>The leak: Agent A's handoff carries a working credential</h2>
<div class=card><p>Candidate <b>{e(s2['title'])}</b>. The summary plus the original incident context were preserved verbatim, so the credential entered the memory candidate.</p>
<pre>{e(s2['leak_line'])}</pre><p class=mut>Frozen candidate SHA-256: <code>{e(s2['hash'])}</code></p></div>

<h2><span class=n>3</span>Before: unprotected namespace (breach)</h2>
<div class=grid>
<div class="card bad"><span class=tag>Agent B search response</span><pre>{e(jscrub(s3['search']))}</pre></div>
<div class="card bad"><span class=tag>Damage</span><p>Credential retrieved: <b>{s_(s3['breach']['canary_found'])}</b> (<code>{REDACTED}</code>)</p>
<p>CRM call with it: <b>HTTP {s_(s3['breach']['crm_status'])}</b></p>
<pre>Alpha record returned to Beta:
customer_name: {e(crm_rec.get('customer_name'))}
purchase:      {e(crm_rec.get('purchase'))}
address:       [REDACTED]</pre></div></div>

<h2><span class=n>4</span>After: protected namespace (blocked)</h2>
<div class=grid>
<div class="card good"><span class=tag>Identical candidate</span>
<p>unprotected SHA-256 <code>{e(s4['hash_u'][:16])}...</code><br>protected SHA-256 <code>{e(s4['hash_p'][:16])}...</code><br>match: <b>{s_(s4['hash_u'] == s4['hash_p'])}</b></p>
<pre>write -> state: {e(s4['create'].get('state'))}
reason_codes: {e(s4['create'].get('reason_codes'))}</pre></div>
<div class="card good"><span class=tag>Agent B retrieval response</span><pre>search: {e(json.dumps(s4['search']))}
guessed-ID lookup: HTTP {s_(s4['guess_status'])}
credential found: {s_(s4['breach']['canary_found'])}  CRM called: {s_(s4['breach']['crm_called'])}</pre></div>
<div class="card good"><span class=tag>Python bypass script</span><pre>raw HTTP POST -> state: {e(s4['raw']['state'])}
reason_codes: {e(s4['raw']['reason_codes'])}
(same decision as the client path)</pre></div>
<div class="card good"><span class=tag>Permissions preserved</span><p>Alpha reads its approved memory: {s_(s4['a_hits'])} hit(s). Beta search: {s_(s4['b_hits'])} hit(s); direct ID: HTTP {s_(s4['b_direct'])}.</p>
<p>Direct Senso write without backend key: <code>{e(s4['senso'])}</code></p></div></div>

<h2><span class=n>5</span>Useful work still happens</h2>
<div class="card good"><p>Agent B published a public-source-only note: <a href="{e(s5['url'])}">{e(s5['url'])}</a> (HTTP {s_(s5['http'])}). Served by the local mock gateway while this demo runs.</p>
<p>Source: <a href="{e(s5['source'])}">{e(s5['source'])}</a> {'<span class=stub>note text is STUB</span>' if s5['llm_stub'] else ''}</p>
<pre>{e(s5['page_text'])}</pre></div>

<h2><span class=n>6</span>The trace: metadata only</h2>
<div class=card><p class=mut>Operation, transport, decision, reason code and measured timings. No secret, address, query or token. Timings are measured from the <b>mock</b> gateway ({R['events']} events); ClickHouse is not connected in this run.</p>
{decisions_table_html(R['rows'])}</div>

<h2>Acceptance checks <span class="{'pass' if allp else 'fail'}">{'ALL PASS' if allp else 'FAILURES'}</span></h2>
<div class="card tw"><table><thead><tr><th>check</th><th>result</th><th>evidence</th></tr></thead><tbody>{chk}</tbody></table></div>
</main>
"""


def s_(x):
    return e(x)


# ----------------------------------------------------------------- entry
def main():
    procs = Procs()
    try:
        procs.start("protected gateway", ["demo/mock_gateway.py"],
                    {"GATEWAY_NAMESPACE": "protected", "GATEWAY_PORT": "8808"}, 8808)
        procs.start("unprotected gateway", ["demo/mock_gateway.py"],
                    {"GATEWAY_NAMESPACE": "unprotected", "GATEWAY_PORT": "8809"}, 8809)
        procs.start("CRM fixture", ["demo/crm_fixture.py"], {}, 8900)
        print("Agent Memory Gateway demo (synthetic data; mock gateways; "
              f"AkashML {'STUB' if akash_client.is_stub() else 'LIVE'})")
        R, checks = run_story(procs)
    finally:
        procs.stop()
    OUT.mkdir(parents=True, exist_ok=True)
    doc = build_html(R, checks)
    (OUT / "index.html").write_text(doc)
    print(f"\n{'=' * 78}\nACCEPTANCE CHECKS\n{'=' * 78}")
    for n, ok, ev in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}  [{ev}]")
    html_leaks = [f for f in FORBIDDEN if f in doc]
    print(f"  {'PASS' if not html_leaks else 'FAIL'}  HTML report contains no raw canary/address/token")
    ok = all(c[1] for c in checks) and not html_leaks
    print(f"\nWrote {OUT / 'index.html'}\nRESULT: {'ALL PASS' if ok else 'FAILURES'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
