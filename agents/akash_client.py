"""OpenAI-compatible chat client for AkashML (stdlib only).

Real mode: POST $AKASHML_BASE/chat/completions with Bearer $AKASHML_API_KEY.
STUB mode (AKASHML_API_KEY unset): deterministic canned output, every return
carries `_stub: true` and a one-line note is written to stderr. Setting the key
switches to real inference with no other code change.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = "https://api.akashml.com/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"


def is_stub():
    return not os.environ.get("AKASHML_API_KEY")


def _stub(messages, tools, tool_choice):
    print("[akash_client] STUB MODE: AKASHML_API_KEY unset; returning canned output (no inference)",
          file=sys.stderr)
    already_called = any(m.get("role") == "tool" for m in messages)
    if tools and tool_choice != "none" and not already_called:
        name = tools[0]["function"]["name"]
        msg = {"role": "assistant", "content": None, "tool_calls": [{
            "id": "stub_call_1", "type": "function",
            "function": {"name": name, "arguments": "{}"}}]}
        return {"message": msg, "tool_calls": msg["tool_calls"], "model": "stub", "_stub": True}
    user = " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "user").lower()
    if "alpha" in user:
        text = ("[STUB] Troubleshooting summary: Service Plan A device fails to sync after a firmware "
                "update. Per the public runbook: power-cycle the device, re-pair it, then trigger a "
                "manual sync. If it persists, escalate with the account context below.")
    else:
        text = ("[STUB] Troubleshooting note (public runbook only): after a firmware update, if a "
                "device cannot sync, power-cycle the device, re-pair it, then trigger a manual sync.")
    return {"message": {"role": "assistant", "content": text}, "content": text,
            "tool_calls": [], "model": "stub", "_stub": True}


def chat(messages, tools=None, tool_choice=None, timeout=60):
    """Return {"message", "content", "tool_calls", "model", "_stub"}."""
    if is_stub():
        return _stub(messages, tools, tool_choice)
    model = os.environ.get("AKASHML_MODEL", DEFAULT_MODEL)
    base = os.environ.get("AKASHML_BASE_URL", BASE_URL).rstrip("/")
    body = {"model": model, "messages": messages}
    if tools:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                 method="POST")
    req.add_header("Authorization", "Bearer " + os.environ["AKASHML_API_KEY"])
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"AkashML HTTP {e.code}: {e.read()[:300]!r}") from None
    msg = data["choices"][0]["message"]
    return {"message": msg, "content": msg.get("content"), "tool_calls": msg.get("tool_calls") or [],
            "model": data.get("model", model), "_stub": False}
