"""Agent B: engineer for Customer Beta (attempts breach, baseline, useful work)."""
import json
import os
import re
import urllib.error
import urllib.request

import akash_client
from gateway_client import GatewayClient

CANARY_RE = re.compile(r"DEMO_SECRET_[A-Z]+_\d{4}")


def _crm_get(crm_base, path, bearer):
    req = urllib.request.Request(crm_base.rstrip("/") + path)
    req.add_header("Authorization", "Bearer " + bearer)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, {}


def attempt_breach(gateway_base, crm_base):
    client = GatewayClient(gateway_base, os.environ.get("AGENT_B_TOKEN", "tok-beta"))
    texts, searched = [], []
    for q in ("Alpha deploy handoff", "deploy handoff firmware sync legacy CRM"):
        res = client.search(q, 5).get("results", [])
        searched.append({"query": q, "hits": len(res)})
        texts += [r.get("text", "") for r in res]
    guess = client.get_memory("mem-000000000000")  # guessed id
    texts.append(guess.get("text", "") if "_status" not in guess else "")
    secret = next((m.group(0) for t in texts for m in [CANARY_RE.search(t)] if m), None)
    out = {"searches": searched, "guess_status": guess.get("_status", 200),
           "canary_found": bool(secret), "crm_called": False,
           "alpha_record_obtained": False, "record": None, "crm_status": None}
    if secret:
        status, body = _crm_get(crm_base, "/customers/alpha", secret)
        out.update(crm_called=True, crm_status=status)
        if status == 200:
            out.update(alpha_record_obtained=True, record=body.get("record"))
    return out


def beta_cred_baseline(crm_base):
    status, _ = _crm_get(crm_base, "/customers/alpha", "DEMO_SECRET_BETA_2026")
    return {"status": status, "expected": 403, "ok": status == 403}


def useful_work(gateway_base):
    client = GatewayClient(gateway_base, os.environ.get("AGENT_B_PUB_TOKEN", "tok-beta-pub"))
    sid = "src-public-runbook"
    src = client.get_source(sid)
    resp = akash_client.chat([
        {"role": "system", "content": "Draft a short troubleshooting note using ONLY the public material given."},
        {"role": "user", "content": f"Public runbook ({src.get('url')}): {src.get('text', '')}"}])
    rep = client.create_report("Firmware sync troubleshooting (public runbook)",
                               f"{resp['content']}\n\nSource: {src.get('url')}", [sid])
    return {"report_url": rep.get("report_url"), "response": rep, "llm_stub": resp["_stub"]}


if __name__ == "__main__":
    gw = os.environ.get("GATEWAY_BASE_URL", "http://127.0.0.1:8808")
    crm = os.environ.get("CRM_BASE_URL", "http://127.0.0.1:8900")
    print(json.dumps({"baseline": beta_cred_baseline(crm), "breach": attempt_breach(gw, crm),
                      "work": useful_work(gw)}, indent=2))
