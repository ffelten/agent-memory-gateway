"""Self-running contract tests (no pytest). Starts the mock gateway in-process
in both namespaces and checks the acceptance scenarios.
Run: uv run python3 tests/test_gateway_contract.py
"""
import os
import sys
import threading
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "demo"))
sys.path.insert(0, os.path.join(ROOT, "agents"))
from mock_gateway import make_server  # noqa: E402
from gateway_client import GatewayClient  # noqa: E402

CANARY = "DEMO_SECRET_ALPHA_2026"
CANDIDATE = ("Incident handoff: Service Plan A customer cannot sync after firmware update. "
             "Runbook: power-cycle and re-pair device. "
             f"Engineer pulled the account via legacy CRM using bearer {CANARY}.")
BENIGN = "Alpha record: Avery Example, 100 Example Lane, Service Plan A, firmware sync issue."

failures = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + ("" if cond else f"  [{detail}]"))
    if not cond:
        failures.append(name)


def start(ns, port):
    srv = make_server(ns, port=port, audit=lambda line: None)  # silence audit in tests
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    return url, {n: GatewayClient(url, t) for n, t in
                 (("alpha", "tok-alpha"), ("beta", "tok-beta"), ("pub", "tok-beta-pub"))}


def main():
    # --- Breach baseline (unprotected) ---
    _, u = start("unprotected", 18809)
    r = u["alpha"].create_memory("Incident handoff", CANDIDATE, "k1")
    check("baseline: secret candidate APPROVED in unprotected ns", r.get("state") == "APPROVED", r)
    check("baseline: create response does not echo text", CANARY not in str(r), r)
    res = u["beta"].search("firmware sync customer")["results"]
    check("baseline: beta search returns canary", any(CANARY in x["text"] for x in res), res)

    # --- Secret admission (protected) ---
    url, p = start("protected", 18808)
    r = p["alpha"].create_memory("Incident handoff", CANDIDATE, "k1")
    mid = r.get("memory_id")
    check("protected: QUARANTINED + SECRET_MATCH",
          r.get("state") == "QUARANTINED" and r.get("reason_codes") == ["SECRET_MATCH"], r)
    check("protected: status endpoint agrees", p["alpha"].get_status(mid).get("state") == "QUARANTINED")
    check("protected: beta search returns nothing", p["beta"].search("firmware sync customer")["results"] == [])
    check("protected: alpha search returns nothing for quarantined",
          p["alpha"].search("firmware sync customer")["results"] == [])
    check("protected: beta GET memory -> 404", p["beta"].get_memory(mid).get("_status") == 404)
    check("protected: alpha GET quarantined -> 404", p["alpha"].get_memory(mid).get("_status") == 404)
    check("protected: beta cannot read status", p["beta"].get_status(mid).get("_status") == 404)

    # --- Script parity (fresh raw HTTP, same content, new key) ---
    req = urllib.request.Request(url + "/v1/memories", method="POST",
                                 data=('{"title":"Incident handoff","text":%s}' % __import__("json").dumps(CANDIDATE)).encode(),
                                 headers={"Authorization": "Bearer tok-alpha", "Idempotency-Key": "k-script",
                                          "Content-Type": "application/json"})
    r2 = __import__("json").loads(urllib.request.urlopen(req).read())
    check("parity: raw HTTP script gets same QUARANTINED decision", r2.get("state") == "QUARANTINED", r2)

    # --- Permission preservation ---
    r = p["alpha"].create_memory("Alpha record", BENIGN, "k-benign")
    check("permission: benign Alpha record APPROVED", r.get("state") == "APPROVED", r)
    check("permission: alpha search finds it",
          any(x["memory_id"] == r["memory_id"] for x in p["alpha"].search("Avery firmware")["results"]))
    check("permission: beta search does NOT", p["beta"].search("Avery firmware")["results"] == [])
    check("permission: beta direct ID -> 404", p["beta"].get_memory(r["memory_id"]).get("_status") == 404)

    # --- Source authorization ---
    check("source: beta denied alpha source", p["beta"].get_source("src-alpha-private").get("_status") == 403)
    check("source: alpha reads alpha source", "text" in p["alpha"].get_source("src-alpha-private"))
    check("auth: bad token -> 401", GatewayClient(url, "nope").search("x").get("_status") == 401)

    # --- Useful work ---
    rep = p["pub"].create_report("Firmware sync runbook", "Power-cycle, re-pair, sync.", ["src-public-runbook"])
    check("report: publishing run gets report_url", "report_url" in rep and "_status" not in rep, rep)
    page = urllib.request.urlopen(rep["report_url"]).read().decode()
    check("report: page renders", "Power-cycle" in page)
    xss = p["pub"].create_report("t", "<script>alert(1)</script>", ["src-public-runbook"])
    check("report: HTML escaped", "<script>alert" not in urllib.request.urlopen(xss["report_url"]).read().decode())
    check("report: private source rejected",
          "_status" in p["pub"].create_report("t", "x", ["src-public-runbook", "src-alpha-private"]))
    check("report: non-publishing run rejected",
          p["beta"].create_report("t", "x", ["src-public-runbook"]).get("_status") == 403)
    check("report: secret text rejected",
          "_status" in p["pub"].create_report("t", f"token={CANARY}", ["src-public-runbook"]))

    # --- Idempotency ---
    a = p["alpha"].create_memory("Idem", "same content here", "k-idem")
    b = p["alpha"].create_memory("Idem", "same content here", "k-idem")
    c = p["alpha"].create_memory("Idem", "different content", "k-idem")
    check("idempotency: same key+content -> same memory_id", a["memory_id"] == b["memory_id"], (a, b))
    check("idempotency: same key+different content -> 409", c.get("_status") == 409, c)

    # --- Revocation ---
    ok = p["alpha"].get_memory(a["memory_id"])
    req = urllib.request.Request(url + f"/v1/admin/memories/{a['memory_id']}/revoke", method="POST",
                                 data=b'{"reason_code":"TEST"}',
                                 headers={"Authorization": "Bearer admin-demo-token", "Content-Type": "application/json"})
    rv = __import__("json").loads(urllib.request.urlopen(req).read())
    check("revoke: readable before, REVOKED + unreadable after",
          "text" in ok and rv["state"] == "REVOKED" and p["alpha"].get_memory(a["memory_id"]).get("_status") == 404)

    print(f"\n{len(failures)} failure(s)" if failures else "\nALL CHECKS PASSED")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
