"""MOCK Agent Memory Gateway -- for local agent/demo testing ONLY.

This is NOT the real AWS gateway Ash is building. It keeps everything in an
in-memory store (no Senso, no DynamoDB, no ClickHouse) and exists so Agents A/B
and the contract tests can run without any cloud dependency. It follows the
route contract in docs/spec/agent-memory-gateway-design.md.

Run:   GATEWAY_NAMESPACE=protected|unprotected uv run python3 demo/mock_gateway.py
Env:   GATEWAY_NAMESPACE (default protected), GATEWAY_PORT (8808),
       GATEWAY_ADMIN_TOKEN (admin-demo-token), GATEWAY_TOKEN_ALPHA / _BETA /
       _BETA_PUB (tok-alpha / tok-beta / tok-beta-pub), GATEWAY_COMPILE_DELAY
       (seconds INGESTING lasts before APPROVED; default 0 = instant).

Identity is ALWAYS derived from the bearer token server-side; the namespace
(protected/unprotected) is ALWAYS server config, never a request field.
Stdlib only.
"""
import hashlib
import html
import json
import os
import re
import signal
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CANARY = "DEMO_SECRET_ALPHA_2026"
MAX_BODY = 32 * 1024  # 32 KiB demo limit
PUBLIC_URL = "https://docs.senso.ai/docs/knowledge-base"

# Credential-shaped patterns (spec: canary + common credential shapes).
SECRET_PATTERNS = [
    re.compile(r"Bearer\s+\S+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*\S{6,}"),
]

# Registered sources: audience is the set of tenants allowed to read them.
SOURCES = {
    "src-public-runbook": {
        "audience": "everyone", "public": True, "url": PUBLIC_URL,
        "text": "Runbook: after a firmware update, if a device cannot sync, "
                "power-cycle the device, re-pair it, then trigger a manual sync.",
    },
    "src-alpha-private": {
        "audience": "alpha", "public": False, "url": None,
        "text": "Alpha customer record: Avery Example, 100 Example Lane, Service Plan A.",
    },
}

# Admin run templates: template_id -> run attributes (principal must match).
TEMPLATES = {
    "tpl-alpha": dict(principal="eng-alpha", tenant="alpha", sources=["src-public-runbook", "src-alpha-private"], audience=["alpha"], publishing=False),
    "tpl-beta": dict(principal="eng-beta", tenant="beta", sources=["src-public-runbook"], audience=["beta"], publishing=False),
    "tpl-beta-publish": dict(principal="eng-beta", tenant="beta", sources=["src-public-runbook"], audience=["beta"], publishing=True),
}


def has_secret(*texts):
    """True if any text contains the canary or a credential-shaped pattern."""
    blob = "\n".join(texts)
    return CANARY in blob or any(p.search(blob) for p in SECRET_PATTERNS)


class Store:
    """In-memory authoritative state for one namespace."""

    def __init__(self, namespace, admin_token, seed_tokens, compile_delay=0.0, audit=print):
        self.namespace = namespace
        self.admin_token = admin_token
        self.compile_delay = compile_delay
        self.audit_sink = audit
        self.lock = threading.RLock()
        self.runs = {}        # token -> run dict
        self.memories = {}    # memory_id -> memory dict
        self.idem = {}        # (principal, run_id, key) -> (content_hash, response, memory_id)
        self.reports = {}     # report_id -> {title, text}
        self.senso = None     # trusted baseline harness only: real Senso backend (unprotected ns)
        self.senso_nodes = []  # Senso node ids this process created (cleaned up on exit)
        for run_id, tpl, tok in (("run-alpha", "tpl-alpha", seed_tokens["alpha"]),
                                 ("run-beta", "tpl-beta", seed_tokens["beta"]),
                                 ("run-beta-pub", "tpl-beta-publish", seed_tokens["beta_pub"])):
            self._add_run(run_id, tpl, tok)

    def _add_run(self, run_id, tpl, token):
        t = TEMPLATES[tpl]
        run = dict(run_id=run_id, token=token, principal=t["principal"], tenant=t["tenant"],
                   source_ids=list(t["sources"]), audience=list(t["audience"]),
                   is_publishing=t["publishing"])
        self.runs[token] = run
        return run

    def mint_run(self, template_id, principal_id):
        tpl = TEMPLATES.get(template_id)
        if not tpl or tpl["principal"] != principal_id:
            return None
        run_id = "run-" + uuid.uuid4().hex[:8]
        return self._add_run(run_id, template_id, "tok-" + uuid.uuid4().hex)

    def audit(self, trace_id, run_id, memory_id, op, decision, reason, gate_ms, total_ms):
        """Metadata-only audit line. Never pass raw text, canary, query or token."""
        self.audit_sink(json.dumps(dict(
            event_id=uuid.uuid4().hex[:12], ns=self.namespace, trace_id=trace_id,
            run_id=run_id, memory_id=memory_id, operation=op, decision=decision,
            reason_code=reason, gate_ms=round(gate_ms, 2), total_ms=round(total_ms, 2))))

    def promote_later(self, memory_id):
        """Simulated Senso readiness: INGESTING -> APPROVED after compile_delay."""
        def _go():
            time.sleep(self.compile_delay)
            with self.lock:
                m = self.memories[memory_id]
                if m["state"] == "INGESTING":  # revoked meanwhile -> stays revoked
                    m["state"] = "APPROVED"
        threading.Thread(target=_go, daemon=True).start()


    def senso_ingest_later(self, memory_id):
        """Real Senso admission: ingest, poll until processed, then APPROVED (or FAILED).
        Never prints provider bodies or memory text."""
        def _go():
            state = "FAILED"
            try:
                with self.lock:
                    m = self.memories[memory_id]
                    title, text = m["title"], m["text"]
                t0 = time.perf_counter()
                r = self.senso.ingest(title, text)
                if r.get("node_id"):
                    with self.lock:
                        self.senso_nodes.append(r["node_id"])
                        m["node_id"], m["content_id"] = r["node_id"], r["content_id"]
                    if self.senso.poll_until_ready(r["node_id"], timeout_s=90) == "complete":
                        state = "APPROVED"
                        m["ingest_ms"] = round((time.perf_counter() - t0) * 1000)
            except Exception:  # noqa: BLE001 - provider errors are never surfaced
                pass
            with self.lock:
                if m["state"] == "INGESTING":  # revoked meanwhile -> stays revoked
                    m["state"] = state
        threading.Thread(target=_go, daemon=True).start()

    def senso_cleanup(self):
        n = 0
        for node in list(self.senso_nodes):
            try:
                self.senso.delete(node)
                n += 1
            except Exception:  # noqa: BLE001
                pass
        return n


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MockGateway/0.1"

        def log_message(self, *a):  # silence default access log (could include paths)
            pass

        # ---- helpers ----
        def _send(self, code, obj, ctype="application/json"):
            body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            """Parse JSON body; returns dict or None after sending an error."""
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                self._send(413, {"error": "body_too_large"})
                return None
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError
                return data
            except ValueError:
                self._send(400, {"error": "invalid_json"})
                return None

        def _token(self):
            h = self.headers.get("Authorization", "")
            return h[7:].strip() if h.startswith("Bearer ") else None

        def _run(self):
            """Authenticate run token; sends 401 and returns None on failure."""
            run = store.runs.get(self._token())
            if not run:
                self._send(401, {"error": "unauthorized"})
            return run

        def _admin(self):
            ok = self._token() == store.admin_token
            if not ok:
                self._send(401, {"error": "unauthorized"})
            return ok

        def _check_fields(self, data, allowed):
            """Unknown (e.g. identity/policy) fields are rejected."""
            if set(data) - set(allowed):
                self._send(400, {"error": "unknown_fields"})
                return False
            return True

        # ---- routing ----
        def do_GET(self):
            p = self.path.split("?")[0]
            if p.startswith("/reports/"):
                return self.get_report(p.split("/")[2])
            m = re.fullmatch(r"/v1/sources/([\w-]+)", p)
            if m:
                return self.get_source(m.group(1))
            m = re.fullmatch(r"/v1/memories/([\w-]+)/status", p)
            if m:
                return self.get_status(m.group(1))
            m = re.fullmatch(r"/v1/memories/([\w-]+)", p)
            if m:
                return self.get_memory(m.group(1))
            self._send(404, {"error": "not_found"})

        def do_POST(self):
            p = self.path.split("?")[0]
            if p == "/v1/admin/runs":
                return self.admin_runs()
            if p == "/v1/memories":
                return self.create_memory()
            if p == "/v1/memories/search":
                return self.search()
            if p == "/v1/reports":
                return self.create_report()
            m = re.fullmatch(r"/v1/admin/memories/([\w-]+)/revoke", p)
            if m:
                return self.revoke(m.group(1))
            self._send(404, {"error": "not_found"})

        # ---- handlers ----
        def admin_runs(self):
            if not self._admin():
                return
            d = self._body()
            if d is None or not self._check_fields(d, ["template_id", "principal_id"]):
                return
            with store.lock:
                run = store.mint_run(d.get("template_id"), d.get("principal_id"))
            if not run:
                return self._send(400, {"error": "unknown_template_or_principal"})
            self._send(200, {"run_id": run["run_id"], "token": run["token"], "source_ids": run["source_ids"]})

        def get_source(self, source_id):
            run = self._run()
            if not run:
                return
            if source_id not in run["source_ids"] or source_id not in SOURCES:
                return self._send(403, {"error": "forbidden"})
            s = SOURCES[source_id]
            self._send(200, {"source_id": source_id, "text": s["text"], "url": s["url"]})

        def create_memory(self):
            t0 = time.perf_counter()
            run = self._run()
            if not run:
                return
            key = self.headers.get("Idempotency-Key")
            d = self._body()
            if d is None or not self._check_fields(d, ["title", "text"]):
                return
            title, text = d.get("title"), d.get("text")
            if not key or not isinstance(title, str) or not isinstance(text, str):
                return self._send(400, {"error": "title, text and Idempotency-Key required"})
            chash = hashlib.sha256((title + text).encode()).hexdigest()
            trace_id = uuid.uuid4().hex
            with store.lock:
                prior = store.idem.get((run["principal"], run["run_id"], key))
                if prior:
                    if prior[0] != chash:
                        return self._send(409, {"error": "idempotency_key_reuse"})
                    return self._send(202, prior[1])
                g0 = time.perf_counter()
                # Admission: skipped entirely in the unprotected namespace (breach baseline);
                # that namespace is also a shared pool with no audience restriction ("*").
                flagged = store.namespace == "protected" and has_secret(title, text)
                gate_ms = (time.perf_counter() - g0) * 1000
                mem_id = "mem-" + uuid.uuid4().hex[:12]
                if flagged:
                    state, reasons = "QUARANTINED", ["SECRET_MATCH"]
                else:
                    state, reasons = "INGESTING", []
                store.memories[mem_id] = dict(
                    memory_id=mem_id, creator_run=run["run_id"], title=title, text=text,
                    state=state, reasons=reasons, tenant=run["tenant"],
                    audience=list(run["audience"]) if store.namespace == "protected" else ["*"], version=1, content_hash=chash,
                    source_urls=[SOURCES[s]["url"] for s in run["source_ids"] if SOURCES[s]["url"]])
                if state == "INGESTING":
                    if store.senso:
                        store.senso_ingest_later(mem_id)
                    elif store.compile_delay > 0:
                        store.promote_later(mem_id)
                    else:
                        store.memories[mem_id]["state"] = state = "APPROVED"
                resp = {"memory_id": mem_id, "state": state, "reason_codes": reasons, "trace_id": trace_id}
                store.idem[(run["principal"], run["run_id"], key)] = (chash, resp, mem_id)
            store.audit(trace_id, run["run_id"], mem_id, "create_memory",
                        "DENY" if flagged else "ALLOW", reasons[0] if reasons else "OK",
                        gate_ms, (time.perf_counter() - t0) * 1000)
            self._send(202, resp)

        def get_status(self, memory_id):
            run = self._run()
            if not run:
                return
            with store.lock:
                m = store.memories.get(memory_id)
                if not m or m["creator_run"] != run["run_id"]:
                    return self._send(404, {"error": "not_found"})
                self._send(200, {"state": m["state"], "reason_codes": m["reasons"]})

        def _readable(self, m, run):
            return m["state"] == "APPROVED" and ("*" in m["audience"] or run["tenant"] in m["audience"])

        def search(self):
            t0 = time.perf_counter()
            run = self._run()
            if not run:
                return
            d = self._body()
            if d is None or not self._check_fields(d, ["query", "max_results"]):
                return
            q, k = d.get("query"), d.get("max_results", 5)
            if not isinstance(q, str) or not isinstance(k, int) or k < 1 or k > 5:
                return self._send(400, {"error": "query str and max_results 1..5 required"})
            if store.senso:
                return self._search_senso(run, q, k, t0)
            words = [w for w in re.findall(r"\w+", q.lower()) if len(w) >= 3]
            scored = []
            with store.lock:
                for m in store.memories.values():
                    if not self._readable(m, run):
                        continue
                    hay = (m["title"] + " " + m["text"]).lower()
                    score = sum(w in hay for w in words)
                    if score:
                        scored.append((score, m))
            scored.sort(key=lambda x: -x[0])
            results = [dict(memory_id=m["memory_id"], text=m["text"], source_urls=m["source_urls"],
                            application_version=m["version"]) for _, m in scored[:k]]
            store.audit(uuid.uuid4().hex, run["run_id"], None, "search",
                        "ALLOW", "OK", 0.0, (time.perf_counter() - t0) * 1000)
            self._send(200, {"results": results})

        def _search_senso(self, run, q, k, t0):
            with store.lock:
                by_cid = {m["content_id"]: m for m in store.memories.values()
                          if m.get("content_id") and self._readable(m, run)}
            s0 = time.perf_counter()
            try:
                hits = store.senso.search_context(q, list(by_cid), k)
            except Exception:  # noqa: BLE001 - never echo provider errors
                return self._send(502, {"error": "backend_unavailable"})
            senso_ms = (time.perf_counter() - s0) * 1000
            results, seen = [], set()
            for h in hits:
                m = by_cid.get(h["content_id"])
                if not m or m["memory_id"] in seen or not h.get("chunk_text"):
                    continue
                seen.add(m["memory_id"])
                results.append(dict(memory_id=m["memory_id"], text=h["chunk_text"], source_urls=m["source_urls"],
                                    application_version=m["version"], backend="senso"))
            store.audit(uuid.uuid4().hex, run["run_id"], None, "search", "ALLOW", "OK",
                        0.0, (time.perf_counter() - t0) * 1000)
            self._send(200, {"results": results[:k]})

        def get_memory(self, memory_id):
            run = self._run()
            if not run:
                return
            with store.lock:
                m = store.memories.get(memory_id)
                ok = bool(m) and self._readable(m, run)
                store.audit(uuid.uuid4().hex, run["run_id"], memory_id, "get_memory",
                            "ALLOW" if ok else "DENY", "OK" if ok else "NOT_READABLE", 0.0, 0.0)
                if not ok:  # 404 (not 403) hides existence
                    return self._send(404, {"error": "not_found"})
                self._send(200, dict(memory_id=memory_id, text=m["text"], source_urls=m["source_urls"],
                                     application_version=m["version"]))

        def revoke(self, memory_id):
            if not self._admin():
                return
            d = self._body()
            if d is None or not self._check_fields(d, ["reason_code"]):
                return
            with store.lock:
                m = store.memories.get(memory_id)
                if not m:
                    return self._send(404, {"error": "not_found"})
                m["state"] = "REVOKED"
            store.audit(uuid.uuid4().hex, "admin", memory_id, "revoke", "ALLOW",
                        str(d.get("reason_code", "UNSPECIFIED"))[:40], 0.0, 0.0)
            self._send(200, {"state": "REVOKED"})

        def create_report(self):
            run = self._run()
            if not run:
                return
            d = self._body()
            if d is None or not self._check_fields(d, ["title", "text", "source_ids"]):
                return
            title, text, sids = d.get("title"), d.get("text"), d.get("source_ids")
            if not (isinstance(title, str) and isinstance(text, str) and isinstance(sids, list) and sids):
                return self._send(400, {"error": "title, text, source_ids required"})
            if not run["is_publishing"]:
                return self._send(403, {"error": "not_a_publishing_run"})
            # Every cited source must be assigned to the run AND public.
            if any(s not in run["source_ids"] or not SOURCES.get(s, {}).get("public") for s in sids):
                return self._send(403, {"error": "non_public_source"})
            if has_secret(title, text):
                return self._send(400, {"error": "rejected", "reason_codes": ["SECRET_MATCH"]})
            rid = "rep-" + uuid.uuid4().hex[:10]
            with store.lock:
                store.reports[rid] = dict(title=title, text=text, sources=[SOURCES[s]["url"] for s in sids])
            host, port = self.server.server_address[:2]
            self._send(200, {"report_id": rid, "report_url": f"http://{host}:{port}/reports/{rid}"})

        def get_report(self, rid):
            r = store.reports.get(rid)
            if not r:
                return self._send(404, "<h1>not found</h1>", "text/html")
            links = "".join(f'<li><a href="{html.escape(u)}">{html.escape(u)}</a></li>' for u in r["sources"])
            page = (f"<!doctype html><title>{html.escape(r['title'])}</title>"
                    f"<h1>{html.escape(r['title'])}</h1><pre>{html.escape(r['text'])}</pre>"
                    f"<h2>Sources</h2><ul>{links}</ul>")
            self._send(200, page, "text/html; charset=utf-8")
    return Handler


def make_server(namespace="protected", host="127.0.0.1", port=8808, compile_delay=0.0, audit=print,
                admin_token="admin-demo-token", tokens=None):
    """Build (not start) a server. Used by main() and by the tests."""
    tokens = tokens or {"alpha": "tok-alpha", "beta": "tok-beta", "beta_pub": "tok-beta-pub"}
    store = Store(namespace, admin_token, tokens, compile_delay, audit)
    srv = ThreadingHTTPServer((host, port), make_handler(store))
    srv.store = store
    return srv


if __name__ == "__main__":
    env = os.environ.get
    ns = env("GATEWAY_NAMESPACE", "protected")
    if ns not in ("protected", "unprotected"):
        raise SystemExit("GATEWAY_NAMESPACE must be protected or unprotected")
    srv = make_server(
        ns, port=int(env("GATEWAY_PORT", "8808")), compile_delay=float(env("GATEWAY_COMPILE_DELAY", "0")),
        admin_token=env("GATEWAY_ADMIN_TOKEN", "admin-demo-token"),
        tokens={"alpha": env("GATEWAY_TOKEN_ALPHA", "tok-alpha"), "beta": env("GATEWAY_TOKEN_BETA", "tok-beta"),
                "beta_pub": env("GATEWAY_TOKEN_BETA_PUB", "tok-beta-pub")})
    backend = "in-memory"
    if ns == "unprotected" and env("BASELINE_SENSO") == "1":
        # Trusted baseline harness only: the key lives in this process env, never given to agents.
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "adapters"))
        from senso_adapter import SensoAdapter
        srv.store.senso = SensoAdapter(os.environ["SENSO_API_KEY"], os.environ["SENSO_FOLDER_ID"])
        backend = "real Senso"

        def _bye(*_):
            print(f"cleanup: deleted {srv.store.senso_cleanup()} Senso nodes", flush=True)
            os._exit(0)
        signal.signal(signal.SIGTERM, _bye)
        signal.signal(signal.SIGINT, _bye)
    print(f"MOCK gateway ({ns}, {backend}) on http://{srv.server_address[0]}:{srv.server_address[1]}", flush=True)
    srv.serve_forever()
