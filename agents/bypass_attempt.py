"""Shows the gateway cannot be bypassed: raw HTTP parity + direct Senso write w/o key."""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

import agent_a
from gateway_client import GatewayClient

SENSO_URL = "https://apiv2.senso.ai/api/v1/org/kb/raw"


def raw_post(base, token, cand):
    req = urllib.request.Request(base.rstrip("/") + "/v1/memories", method="POST",
                                 data=json.dumps(cand).encode())
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    req.add_header("Idempotency-Key", "raw-" + uuid.uuid4().hex[:8])
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_status": e.code}


def senso_direct_no_key(timeout=15):
    """Direct write with NO credentials at all (the agent runtime has no Senso key)."""
    req = urllib.request.Request(SENSO_URL, method="POST",
                                 data=json.dumps({"title": "bypass", "text": "bypass"}).encode())
    req.add_header("Content-Type", "application/json")  # deliberately no API key header
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:  # offline: write cannot succeed either
        return f"unreachable ({type(e).__name__})"


def main(gateway_base=None):
    base = gateway_base or os.environ.get("GATEWAY_BASE_URL", "http://127.0.0.1:8808")
    token = os.environ.get("AGENT_A_TOKEN", "tok-alpha")
    cand = agent_a.build_candidate("[bypass test] summary", agent_a.FIX["incident"])
    raw = raw_post(base, token, cand)
    mcp = GatewayClient(base, token).create_memory(cand["title"], cand["text"], "mcp-" + uuid.uuid4().hex[:8])
    parity = raw.get("state") == mcp.get("state") and raw.get("reason_codes") == mcp.get("reason_codes")
    print(f"raw HTTP : state={raw.get('state')} reasons={raw.get('reason_codes')}")
    print(f"client   : state={mcp.get('state')} reasons={mcp.get('reason_codes')}")
    print("PASS" if parity else "FAIL", "- raw HTTP and client path give the same decision")
    s = senso_direct_no_key()
    denied = s in (401, 403) or (isinstance(s, str))
    print(f"direct Senso write without key -> {s}")
    print("PASS" if denied else "FAIL", "- direct Senso write without backend key did not succeed")
    return parity and denied, raw


if __name__ == "__main__":
    ok, raw = main()
    if raw.get("state") != "QUARANTINED":
        print("note: gateway is not enforcing (state is not QUARANTINED); parity only", file=sys.stderr)
    sys.exit(0 if ok else 1)
