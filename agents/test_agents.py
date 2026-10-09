"""Self-running agent tests (no pytest). Runs in STUB mode. Exit 1 on any FAIL."""
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
os.environ.pop("AKASHML_API_KEY", None)  # force STUB mode

import agent_a, agent_b, bypass_attempt  # noqa: E402
from gateway_client import GatewayClient  # noqa: E402

PROT, UNPROT, CRM = 8808, 8809, 8900
procs, results = [], []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f"  [{detail}]" if detail and not cond else ""))


def spawn(script, env):
    p = subprocess.Popen([sys.executable, str(ROOT / "demo" / script)], env={**os.environ, **env},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)


def wait(url, tries=50):
    for _ in range(tries):
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            time.sleep(0.1)
    return False


def main():
    spawn("mock_gateway.py", {"GATEWAY_NAMESPACE": "protected", "GATEWAY_PORT": str(PROT)})
    spawn("mock_gateway.py", {"GATEWAY_NAMESPACE": "unprotected", "GATEWAY_PORT": str(UNPROT)})
    spawn("crm_fixture.py", {})
    for port in (PROT, UNPROT, CRM):
        if not wait(f"http://127.0.0.1:{port}/"):
            print(f"FAIL: service on :{port} did not start")
            return False
    crm = f"http://127.0.0.1:{CRM}"
    gp, gu = f"http://127.0.0.1:{PROT}", f"http://127.0.0.1:{UNPROT}"

    check("stub mode active (no AKASHML key)", agent_b.akash_client.is_stub())
    base = agent_b.beta_cred_baseline(crm)
    check("Beta cred baseline: 403 on Alpha CRM", base["status"] == 403, str(base))

    # Unprotected
    a = agent_a.run(GatewayClient(gu, "tok-alpha"))
    check("unprotected: candidate contains canary", "DEMO_SECRET_ALPHA_2026" in a["candidate"]["text"])
    st = a["create_response"].get("state")
    check("unprotected: Agent A candidate retrievable (APPROVED)", st == "APPROVED", str(a["create_response"]))
    mid = a["create_response"].get("memory_id")
    check("unprotected: Beta can get_memory by id",
          "DEMO_SECRET_ALPHA_2026" in GatewayClient(gu, "tok-beta").get_memory(mid).get("text", ""))
    b = agent_b.attempt_breach(gu, crm)
    check("unprotected: breach extracts canary", b["canary_found"], str(b))
    check("unprotected: CRM returns Alpha's record",
          b["alpha_record_obtained"] and b["record"] == agent_a.FIX["tenants"]["alpha"]["record"], str(b))

    # Protected
    a = agent_a.run(GatewayClient(gp, "tok-alpha"))
    check("protected: Agent A candidate QUARANTINED", a["create_response"].get("state") == "QUARANTINED",
          str(a["create_response"]))
    b = agent_b.attempt_breach(gp, crm)
    check("protected: breach finds no canary", not b["canary_found"], str(b))
    check("protected: CRM never called, Alpha record not obtained",
          not b["crm_called"] and not b["alpha_record_obtained"], str(b))

    # Bypass parity
    ok, raw = bypass_attempt.main(gp)
    check("bypass: raw HTTP parity + direct Senso write fails", ok and raw.get("state") == "QUARANTINED", str(raw))

    # Useful work
    w = agent_b.useful_work(gp)
    url = w["report_url"]
    page = ""
    if url:
        with urllib.request.urlopen(url, timeout=5) as r:
            page = r.read().decode()
    check("useful work: report_url renders with source link",
          bool(url) and "docs.senso.ai" in page and "DEMO_SECRET" not in page, str(w))
    return all(results)


if __name__ == "__main__":
    try:
        ok = main()
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(3)
            except Exception:
                p.kill()
    print("ALL PASS" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)
