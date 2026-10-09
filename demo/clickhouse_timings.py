"""Read back gateway timings for given trace IDs from ClickHouse (gateway_events). Metadata columns only.

Usage: set -a; . ./.env; set +a; uv run python3 demo/clickhouse_timings.py TRACE_ID [TRACE_ID ...]
Never prints the password; errors are reported as a short generic message.
"""
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request

COLUMNS = "trace_id, operation, transport, decision, reason_code, gate_ms, senso_ms, total_ms, timestamp"
QUERY = (f"SELECT {COLUMNS} FROM gateway_events FINAL WHERE trace_id IN {{ids:Array(String)}} "
         "ORDER BY timestamp FORMAT JSONEachRow")


def configured():
    return all(os.environ.get(k) for k in ("CLICKHOUSE_HOST", "CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD"))


def _query(trace_ids):
    host = os.environ["CLICKHOUSE_HOST"]
    port = os.environ.get("CLICKHOUSE_HTTPS_PORT", "8443")
    auth = base64.b64encode(f"{os.environ['CLICKHOUSE_USER']}:{os.environ['CLICKHOUSE_PASSWORD']}".encode()).decode()
    ids = "[" + ",".join("'" + t.replace("\\", "\\\\").replace("'", "\\'") + "'" for t in trace_ids) + "]"
    url = f"https://{host}:{port}/?" + urllib.parse.urlencode({"param_ids": ids})
    req = urllib.request.Request(url, data=QUERY.encode(), method="POST", headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return [json.loads(line) for line in resp.read().decode().splitlines() if line.strip()]


def fetch(trace_ids, timeout_s=60):
    """Poll until every trace ID has at least one row or timeout. Returns rows (list of dicts); [] on error/unconfigured."""
    ids = sorted({t for t in trace_ids if t})
    if not ids or not configured():
        return []
    deadline = time.time() + timeout_s
    rows = []
    while True:
        try:
            rows = _query(ids)
        except Exception:
            rows = rows or []
        if {r["trace_id"] for r in rows} >= set(ids) or time.time() >= deadline:
            return rows
        time.sleep(3)


if __name__ == "__main__":
    out = fetch(sys.argv[1:], timeout_s=15)
    for r in out:
        print(json.dumps(r))
    print(f"{len({r['trace_id'] for r in out})} of {len(set(sys.argv[1:]))} trace IDs found", file=sys.stderr)
