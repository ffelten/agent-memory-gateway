"""Agent A: engineer for Customer Alpha. Writes a handoff memory via the gateway."""
import json
import os
import pathlib
import uuid

import akash_client
from gateway_client import GatewayClient

FIX = json.loads((pathlib.Path(__file__).resolve().parent.parent / "contracts" / "fixtures.json").read_text())
SOURCE_IDS = ["src-public-runbook", "src-alpha-private"]


def build_candidate(summary, incident):
    """Handoff memory: generated summary + the ORIGINAL incident context, preserved verbatim.

    The incident's legacy_api_note carries the credential (this models the real
    leak: context is carried along, the credential is not injected on purpose).
    """
    title = "Alpha deploy handoff: device sync after firmware update"
    text = (f"{summary}\n\n--- Original incident context (preserved for handoff) ---\n"
            f"Customer: {incident['customer']}\n"
            f"Symptom: {incident['symptom']}\n"
            f"Notes: {incident['legacy_api_note']}\n")
    return {"title": title, "text": text}


def run(client=None):
    client = client or GatewayClient(os.environ.get("GATEWAY_BASE_URL", "http://127.0.0.1:8808"),
                                     os.environ.get("GATEWAY_TOKEN_ALPHA")
                                     or os.environ.get("AGENT_A_TOKEN", "tok-alpha"))
    sources = {sid: client.get_source(sid) for sid in SOURCE_IDS}
    incident = FIX["incident"]
    runbook = sources["src-public-runbook"].get("text", "")
    resp = akash_client.chat([
        {"role": "system", "content": "You are a support engineer. Write a short troubleshooting summary."},
        {"role": "user", "content": f"Customer {incident['customer']} incident: {incident['symptom']}\n"
                                    f"Public runbook: {runbook}"}])
    candidate = build_candidate(resp["content"], incident)
    create_response = client.create_memory(candidate["title"], candidate["text"],
                                           "agent-a-" + uuid.uuid4().hex[:8])
    settled = create_response.get("state")
    if settled in GatewayClient.PENDING_STATES and create_response.get("memory_id"):
        settled = client.wait_until_settled(create_response["memory_id"])  # real gateway: async ingest
    return {"candidate": candidate, "create_response": create_response, "settled_state": settled,
            "sources_fetched": {k: v.get("_status", 200) for k, v in sources.items()},
            "llm_stub": resp["_stub"]}


if __name__ == "__main__":
    out = run()
    print(json.dumps({"title": out["candidate"]["title"], "create_response": out["create_response"]}, indent=2))
