"""Small stdlib client for the Agent Memory Gateway (mock or real).

Usage:  c = GatewayClient("http://127.0.0.1:8808", "tok-alpha")
Every method returns the parsed JSON dict. On a non-2xx response the dict
also carries "_status": <http code>. Switching from the mock to the real
gateway only requires changing base_url and token. Identity comes from the
bearer token on the server; this client never sends identity fields.
"""
import json
import time
import urllib.error
import urllib.request


class GatewayClient:
    def __init__(self, base_url, token, timeout=15):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return self._parse(r.read(), r.status, ok=True)
        except urllib.error.HTTPError as e:
            return self._parse(e.read(), e.code, ok=False)

    @staticmethod
    def _parse(raw, status, ok):
        try:
            out = json.loads(raw) if raw else {}
            if not isinstance(out, dict):
                out = {"data": out}
        except ValueError:
            out = {"raw": raw.decode(errors="replace")[:500]}
        if not ok:
            out["_status"] = status
        return out

    def get_source(self, source_id):
        return self._request("GET", f"/v1/sources/{source_id}")

    def create_memory(self, title, text, idempotency_key):
        return self._request("POST", "/v1/memories", {"title": title, "text": text},
                             {"Idempotency-Key": idempotency_key})

    def get_status(self, memory_id):
        return self._request("GET", f"/v1/memories/{memory_id}/status")

    PENDING_STATES = ("INGESTING", "PENDING")

    def wait_until_settled(self, memory_id, timeout_s=90, first_delay=0.1, max_delay=3.0):
        """Poll the creator-only status route with bounded backoff until the memory
        leaves INGESTING/PENDING. Returns the final state string (APPROVED,
        QUARANTINED, REVOKED, ...), the still-pending state on timeout, or
        "HTTP_<code>" if the status call itself fails. The full last status
        response is kept in self.last_status.
        """
        deadline = time.monotonic() + timeout_s
        delay = first_delay
        while True:
            st = self.get_status(memory_id)
            self.last_status = st
            if "_status" in st:
                return f"HTTP_{st['_status']}"
            state = st.get("state")
            if state not in self.PENDING_STATES or time.monotonic() >= deadline:
                return state
            time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
            delay = min(delay * 1.6, max_delay)

    def search(self, query, max_results=5):
        return self._request("POST", "/v1/memories/search", {"query": query, "max_results": max_results})

    def get_memory(self, memory_id):
        return self._request("GET", f"/v1/memories/{memory_id}")

    def create_report(self, title, text, source_ids):
        return self._request("POST", "/v1/reports", {"title": title, "text": text, "source_ids": list(source_ids)})
