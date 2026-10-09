"""LIVE round-trip test: ingest -> poll -> scoped search -> empty guard -> delete."""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import senso_adapter
from senso_adapter import SensoAdapter

ENV = "/Users/florian/Documents/hackathon/.env"
failed = []


def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), "-", name, extra)
    if not cond:
        failed.append(name)
    return cond


def load_env():
    env = {}
    for line in open(ENV):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main():
    env = load_env()
    ad = SensoAdapter(env["SENSO_API_KEY"], env["SENSO_FOLDER_ID"])
    tag = uuid.uuid4().hex
    node_id = None
    try:
        r = ad.ingest(f"Gateway test note {tag[:8]}",
                      f"Project Zephyrwick {tag}: the team's favourite deployment day is Tuesday, "
                      f"and the staging cluster is named Marmalade-{tag[:6]}.")
        node_id = r.get("node_id")
        cid = r.get("content_id")
        check("1 ingest returns node_id+content_id", bool(node_id and cid), f"status={r.get('status')}")
        if not (node_id and cid):
            return
        st = ad.poll_until_ready(node_id, timeout_s=60, interval_s=3)
        check("2 poll_until_ready == complete", st == "complete", f"got={st}")
        res = []
        import time
        for _ in range(5):  # index may lag slightly after "complete"
            res = ad.search_context("What is the staging cluster named?", [cid])
            if res:
                break
            time.sleep(3)
        check("3 scoped search finds passage w/ matching content_id",
              bool(res) and all(p["content_id"] == cid for p in res),
              f"n={len(res)} keys={sorted(res[0]) if res else None}")
        calls = []
        orig = ad._request
        ad._request = lambda *a, **k: calls.append(a) or orig(*a, **k)
        e1, e2 = ad.search_context("anything", []), ad.search_context("anything", None)
        ad._request = orig
        check("4 empty content_ids -> [] with no network call", e1 == [] and e2 == [] and not calls)
        ok = ad.delete(node_id)
        check("5 delete returns True", ok is True)
        node_id = None
        try:
            post = ad.search_context("What is the staging cluster named?", [cid])
            print("INFO post-delete scoped search results:", len(post))
        except senso_adapter.SensoError as e:
            print("INFO post-delete search error:", e.status)
    except Exception as e:
        check("unexpected exception", False, f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        if node_id:
            try:
                ad.delete(node_id)
                print("INFO cleanup delete done")
            except Exception as e:
                print("INFO cleanup failed:", type(e).__name__)


main()
print("RESULT:", "FAIL" if failed else "ALL PASS")
sys.exit(1 if failed else 0)
