"""Agent B: engineer for Customer Beta (attempts breach, baseline, useful work)."""
import json
import os
import re
import urllib.error
import urllib.request
import time
import uuid

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


def beta_token():
    return os.environ.get("GATEWAY_TOKEN_BETA") or os.environ.get("AGENT_B_TOKEN", "tok-beta")


def beta_pub_token():
    return os.environ.get("GATEWAY_TOKEN_BETA_PUB") or os.environ.get("AGENT_B_PUB_TOKEN", "tok-beta-pub")


def attempt_breach(gateway_base, crm_base, token=None):
    client = GatewayClient(gateway_base, token or beta_token())
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


def _source_topic(src):
    """Short topic from the registered source itself (its title, else first text line, else URL slug)."""
    raw = src.get("title") or next((ln for ln in (src.get("text") or "").splitlines() if ln.strip()), "")
    topic = re.sub(r"\s+", " ", raw.lstrip("# ").strip())[:80].strip()
    if not topic and src.get("url"):
        topic = src["url"].rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
    return topic or "public source"


def useful_work(gateway_base, token=None, create_runbook_memory=True):
    """Publish a source-linked note drafted from RETRIEVED approved public memory.

    Flow: read the public source, write it as a memory (polling until the async
    gateway settles it), search for it through the gateway, draft the note from the
    retrieved text only, then publish. create_runbook_memory is kept for caller
    compatibility; the runbook memory is now always written (retrieval needs it). The memory is written by this public-only run from the registered
    public source, never from any other tenant's handoff. Titles/query derive from the source.
    """
    client = GatewayClient(gateway_base, token or beta_pub_token())
    sid = "src-public-runbook"
    src = client.get_source(sid)
    src_url = src.get("url")
    topic = _source_topic(src)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())  # Senso rejects duplicate text
    for attempt in range(3):
        cr = client.create_memory(f"Public source: {topic}",
                                  f"{src.get('text', '')}\n\nSource: {src_url}\nAdded to memory: {stamp}",
                                  "runbook-" + uuid.uuid4().hex[:8])
        # Retry only a transient provider outage, never a policy decision.
        if cr.get("reason_codes") != ["PROVIDER_UNAVAILABLE"]:
            break
        time.sleep(5 * (attempt + 1))
    mem_id = cr.get("memory_id")
    state = cr.get("state")
    if state in GatewayClient.PENDING_STATES and mem_id:
        state = client.wait_until_settled(mem_id)
    runbook = {"memory_id": mem_id, "create_state": cr.get("state"), "settled_state": state, "reason_codes": cr.get("reason_codes")}

    results = client.search(topic, 5).get("results", []) or []
    hit = next((r for r in results if mem_id and r.get("memory_id") == mem_id), None)
    matched = hit is not None
    if hit is None:
        hit = next((r for r in results if src_url and src_url in (r.get("text") or "")), None)
    retrieval = {"memory_id": hit.get("memory_id") if hit else None, "hits": len(results),
                 "matched": matched, "application_version": hit.get("application_version") if hit else None}
    retrieved_text = (hit.get("text") or "") if hit else ""

    resp = akash_client.chat([
        {"role": "system", "content": "Draft a short troubleshooting note using ONLY the retrieved memory text given. "
                                      "Do not add facts that are not in it."},
        {"role": "user", "content": f"Retrieved approved public memory:\n{retrieved_text}"}])
    rep = client.create_report(f"Public note: {topic}",
                               f"{resp['content']}\n\nSource: {src_url}", [sid])
    return {"report_url": rep.get("report_url"), "response": rep, "runbook_memory": runbook,
            "retrieval": retrieval, "llm_stub": resp["_stub"]}


if __name__ == "__main__":
    gw = os.environ.get("GATEWAY_BASE_URL", "http://127.0.0.1:8808")
    crm = os.environ.get("CRM_BASE_URL", "http://127.0.0.1:8900")
    print(json.dumps({"baseline": beta_cred_baseline(crm), "breach": attempt_breach(gw, crm),
                      "work": useful_work(gw)}, indent=2))
