"""Run Florian's client + minting harness against Ash's REAL gateway code, served locally.

Starts gateway/local.py from Ash's worktree (env ASH_GATEWAY_DIR) with his in-memory Store, his
bootstrap.seed() sources, and the real policy service (real secret scanner, real run/ACL logic).
The only stand-in is the PROVIDER (Senso): a tiny in-process double so APPROVED states can be
reached; checks that depend on it are labelled [provider double, NOT Senso]. Prints PASS/FAIL/SKIP.
Needs only the stdlib (Ash's local path imports no boto3).
"""
import json
import os
import pathlib
import secrets
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "demo"))
os.environ.pop("AKASHML_API_KEY", None)

import agent_a, agent_b, bypass_attempt  # noqa: E402
import mint_runs  # noqa: E402
from gateway_client import GatewayClient  # noqa: E402

ASH = pathlib.Path(os.environ.get(
    "ASH_GATEWAY_DIR",
    "/private/tmp/claude-501/-Users-florian-Documents-hackathon/6cd52517-dbe8-4086-aa49-6886834b967a/scratchpad/ash-gw"))

LAUNCHER = r'''
import hashlib, os, sys, threading
sys.path.insert(0, os.getcwd())
from gateway.bootstrap import seed
from gateway.local import make_server
from gateway.provider import Document, Passage
from gateway.service import Gateway, digest
from gateway.store import Store

class ProviderDouble:  # stand-in for Senso: instantly "ready"; keeps text for scoped search
    def __init__(self): self.docs = {}
    def ingest(self, *, title, text, external_id):
        self.docs[external_id] = text
        return Document("node-" + external_id, "content-" + external_id, "v1", True)
    def inspect(self, node_id):
        return Document(node_id, "content-" + node_id[5:], "v1", True)
    def search(self, *, query, content_ids, max_results, require_scoped_ids=True):
        return [Passage(c, self.docs.get(c[8:], ""), "v1") for c in content_ids][:max_results]
    def delete(self, node_id): pass

store = Store()
seed(store, "Re-pair devices after a firmware update, then run a manual sync.",
     "https://docs.senso.ai/docs/knowledge-base", "2026-10-09T12:00:00+00:00")
gw = Gateway(store, ProviderDouble(), admin_token_hash=digest(os.environ["ADMIN"]), report_base_url="http://127.0.0.1")
srv = make_server(gw)
gw.report_base_url = 'http://127.0.0.1:%d' % srv.server_port
print(srv.server_port, flush=True)
srv.serve_forever()
'''

results = []


def report(name, status, detail=""):
    results.append(status)
    print(f"{status}: {name}" + (f"  [{detail}]" if detail else ""))


def check(name, cond, detail=""):
    report(name, "PASS" if cond else "FAIL", "" if cond else detail)


def main():
    if not (ASH / "gateway" / "local.py").exists():
        report("real gateway available", "SKIP", f"Ash's worktree not found at {ASH}; set ASH_GATEWAY_DIR")
        return True
    admin = secrets.token_urlsafe(24)
    proc = subprocess.Popen([sys.executable, "-c", LAUNCHER], cwd=str(ASH), env={**os.environ, "ADMIN": admin},
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        port = int(proc.stdout.readline().strip())
        base = f"http://127.0.0.1:{port}"
        envfile = pathlib.Path(tempfile.mkdtemp()) / "runs.env"
        # mint via the real CLI, admin bearer only in that subprocess' env
        cp = subprocess.run([sys.executable, str(ROOT / "demo" / "mint_runs.py")], capture_output=True, text=True,
                            env={**os.environ, "GATEWAY_BASE_URL": base, "GATEWAY_ADMIN_TOKEN": admin,
                                 "GATEWAY_RUNS_ENV_FILE": str(envfile)})
        toks = dict(l.split("=", 1) for l in envfile.read_text().splitlines()) if envfile.exists() else {}
        check("mint_runs.py minted 3 runs", cp.returncode == 0 and len(toks) == 3, cp.stdout + cp.stderr)
        check("mint_runs.py file mode 600", envfile.exists() and (envfile.stat().st_mode & 0o777) == 0o600)
        check("mint_runs.py printed no token/admin bearer",
              not any(v in cp.stdout + cp.stderr for v in list(toks.values()) + [admin]))
        if len(toks) != 3:
            return False
        # agents read the tokens from env, exactly as a real run would (no admin in env)
        os.environ.update(GATEWAY_BASE_URL=base, **toks)
        os.environ.pop("GATEWAY_ADMIN_TOKEN", None)
        a = GatewayClient(base, toks["GATEWAY_TOKEN_ALPHA"])
        b = GatewayClient(base, toks["GATEWAY_TOKEN_BETA"])

        check("bogus bearer -> 401", GatewayClient(base, "bogus-token-123")
              .get_source("src-public-runbook").get("_status") == 401)
        check("Alpha reads src-alpha-private; Beta gets 403",
              "text" in a.get_source("src-alpha-private") and b.get_source("src-alpha-private").get("_status") == 403)

        # Alpha's credential-bearing handoff (agent_a.run reads env tokens/base)
        out = agent_a.run()
        cr = out["create_response"]
        check("Alpha handoff carries the canary", "DEMO_SECRET_ALPHA_2026" in out["candidate"]["text"])
        check("Alpha handoff -> QUARANTINED / SECRET_MATCH",
              cr.get("state") == "QUARANTINED" and cr.get("reason_codes") == ["SECRET_MATCH"], json.dumps(cr))
        check("quarantined candidate: status settled QUARANTINED", out["settled_state"] == "QUARANTINED")

        # Beta breach attempt, guessed IDs
        crm = "http://127.0.0.1:1"  # must never be called
        br = agent_b.attempt_breach(base, crm)
        check("Beta search finds nothing, no canary, CRM not called",
              all(s["hits"] == 0 for s in br["searches"]) and not br["canary_found"] and not br["crm_called"], str(br))
        check("Beta guessed memory id -> 404", br["guess_status"] == 404)
        check("Beta fetch of Alpha's quarantined memory id -> 404", b.get_memory(cr["memory_id"]).get("_status") == 404)
        check("Beta status of Alpha's memory id -> 404", b.get_status(cr["memory_id"]).get("_status") == 404)
        check("Alpha cannot read its own quarantined memory",
              a.get_memory(cr["memory_id"]).get("_status") == 404 and not a.search("Alpha deploy handoff").get("results"))

        # raw HTTP parity (bypass_attempt.raw_post is the standalone-script path)
        raw = bypass_attempt.raw_post(base, toks["GATEWAY_TOKEN_ALPHA"], out["candidate"])
        check("raw HTTP gives same decision as client",
              raw.get("state") == cr["state"] and raw.get("reason_codes") == cr["reason_codes"], json.dumps(raw))
        check("create response shape has trace_id, no echoed text", "trace_id" in cr and "DEMO_SECRET" not in json.dumps(cr))

        # INGESTING polling + approval + permission preservation  [provider double]
        tag = "[provider double, NOT Senso]"
        clean = a.create_memory("Alpha approved note", "Service Plan A devices need re-pairing after a firmware update.",
                                "clean-1")
        check("clean write returns INGESTING or APPROVED receipt with memory_id",
              clean.get("state") in ("INGESTING", "APPROVED") and "memory_id" in clean, json.dumps(clean))
        st = a.wait_until_settled(clean["memory_id"], timeout_s=20)
        check(f"wait_until_settled -> APPROVED {tag}", st == "APPROVED", f"state={st}")
        hits = a.search("re-pairing firmware").get("results", [])
        check(f"Alpha search returns its approved memory {tag}", any(h["memory_id"] == clean["memory_id"] for h in hits))
        check(f"Beta cannot search/get Alpha's approved memory {tag}",
              not b.search("re-pairing firmware").get("results")
              and b.get_memory(clean["memory_id"]).get("_status") == 404)

        # publishing run
        w = agent_b.useful_work(base, create_runbook_memory=True)
        url = w["report_url"]
        check("publishing run created a report", bool(url) and "/reports/" in url, json.dumps(w["response"]))
        rb = w["runbook_memory"] or {}
        check(f"public runbook memory created under publishing run, settled APPROVED {tag}",
              rb.get("settled_state") == "APPROVED", str(rb))
        check("non-publishing Beta run cannot create a report",
              b.create_report("x", "y", ["src-public-runbook"]).get("_status") == 403)
        report("Real Senso ingest/readiness/version check", "SKIP",
               "needs Senso API key + network (Ash's provider bridge); local run uses a provider double")
        report("AWS/Lambda/DynamoDB, ClickHouse audit export, rendered report page over HTTPS", "SKIP",
               "local server has no AWS deployment; report URL is http://127.0.0.1:<port>, not HTTPS")
    finally:
        proc.terminate()
        try:
            proc.wait(3)
        except Exception:
            proc.kill()
    return "FAIL" not in results


if __name__ == "__main__":
    ok = main()
    print("ALL PASS (SKIPs listed above)" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)
