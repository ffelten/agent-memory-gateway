"""Senso backend adapter. Key is injected by the caller; never logged, never read from env here."""
import json
import time
import urllib.error
import urllib.request


class SensoError(Exception):
    def __init__(self, status, message):
        super().__init__(f"Senso error {status}: {message}")
        self.status = status
        self.message = message


class SensoAdapter:
    def __init__(self, api_key, folder_id, base_url="https://apiv2.senso.ai/api/v1", timeout=30):
        self._api_key = api_key
        self.folder_id = folder_id
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def __repr__(self):
        return f"SensoAdapter(base_url={self.base_url!r}, folder_id={self.folder_id!r})"

    def _request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("X-API-Key", self._api_key)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "agent-memory-gateway/0.1")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            raw = e.read() or b""
            # Error message: only a short server-provided detail, never request content.
            msg = raw.decode("utf-8", "replace")[:300] or e.reason
            raise SensoError(e.code, msg) from None
        except urllib.error.URLError as e:
            raise SensoError(0, f"network error: {e.reason}") from None
        if not raw:
            return status, {}
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, {}

    def ingest(self, title, text):
        _, d = self._request("POST", "/org/kb/raw", {
            "title": title, "text": text, "kb_folder_node_id": self.folder_id})
        return {"node_id": d.get("kb_node_id"), "content_id": d.get("id"),
                "status": d.get("processing_status", "processing")}

    def poll_until_ready(self, node_id, timeout_s=60, interval_s=3):
        deadline = time.monotonic() + timeout_s
        last = "processing"
        while True:
            _, d = self._request("GET", f"/org/kb/nodes/{node_id}")
            content = d.get("content") if isinstance(d.get("content"), dict) else {}
            st = content.get("processing_status") or d.get("processing_status") or "processing"
            last = st
            if st == "complete":
                return "complete"
            if st in ("failed", "error"):
                return "failed"
            if time.monotonic() + interval_s > deadline:
                return "timeout"
            time.sleep(interval_s)

    def search_context(self, query, content_ids, max_results=5):
        if not content_ids:
            return []
        body = {"query": query, "max_results": min(int(max_results), 20),
                "content_ids": list(content_ids), "require_scoped_ids": True}
        try:
            _, d = self._request("POST", "/org/search/context", body)
        except SensoError as e:
            if e.status != 404:
                raise
            _, d = self._request("POST", "/org/search", body)
        if isinstance(d, list):
            items = d
        else:
            items = d.get("results") or d.get("passages") or []
        allowed = set(content_ids)
        out = []
        for it in items:
            cid = it.get("content_id")
            if cid not in allowed:  # defense in depth: never leak out-of-scope passages
                continue
            out.append({"content_id": cid, "chunk_text": it.get("chunk_text"),
                        "title": it.get("title"), "score": it.get("score")})
        return out

    def delete(self, node_id):
        try:
            self._request("DELETE", f"/org/kb/nodes/{node_id}")
        except SensoError as e:
            if e.status == 404:
                return True
            raise
        return True
