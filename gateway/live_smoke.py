"""Operator-only live checks. Never print credentials, source text, or responses.

Run from the repository with ``python -m gateway.live_smoke --base-url HTTPS_URL``.
The sole admin credential source is the ignored .gateway-admin.json file.
"""

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from uuid import uuid4


CANARY = "DEMO_SECRET_ALPHA_2026"
PUBLIC_SOURCE = "src-public-runbook"
PRIVATE_SOURCE = "src-alpha-private"
_TRACE = re.compile(r"[a-f0-9]{32}")
_TOKEN = re.compile(r"[A-Za-z0-9._~-]{8,256}")
_CREDENTIAL_HINT = re.compile(r"demo_secret|bearer|password|private key|api[_ -]?key|secret|token", re.I)
_AUDIT_FIELDS = frozenset({
    "event_id", "trace_id", "tenant_id", "principal_id", "run_id", "memory_id", "application_version",
    "operation", "transport", "decision", "reason_code", "policy_version", "gate_ms", "senso_ms", "total_ms",
    "timestamp", "delivered", "_rev",
})


class SmokeFailure(Exception):
    """An intentionally detail-free failure at the operator boundary."""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpTransport:
    def __init__(self, base_url):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or re.search(r"\s", base_url)):
            raise SmokeFailure()
        self.base_url = base_url.rstrip("/")
        self.opener = build_opener(_NoRedirects(), HTTPSHandler(context=ssl.create_default_context()))

    def __call__(self, method, path, token=None, body=None, key=None, timeout=30):
        headers = {"Accept": "application/json, text/html"}
        if token:
            headers["Authorization"] = "Bearer "+token
        if key:
            headers["Idempotency-Key"] = key
        encoded = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            encoded = json.dumps(body).encode("utf-8")
        request = Request(self.base_url+path, data=encoded, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read(1024*1024+1)
            if len(raw) > 1024*1024:
                raise SmokeFailure()
            content_type = response.headers.get("Content-Type", "").lower()
            result = raw.decode("utf-8")
            if "application/json" in content_type:
                result = json.loads(result)
            return response.code, result, dict(response.headers)


def _write_private(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _public_excerpt(text):
    for line in text.splitlines():
        candidate = line.strip()[:500]
        if len(candidate) >= 20 and not _CREDENTIAL_HINT.search(candidate):
            return candidate
    raise SmokeFailure()


def _dynamo_value(value):
    if not isinstance(value, dict) or len(value) != 1:
        raise SmokeFailure()
    kind, item = next(iter(value.items()))
    if kind == "M":
        return {key: _dynamo_value(nested) for key, nested in item.items()}
    if kind == "L":
        return [_dynamo_value(nested) for nested in item]
    if kind in {"S", "N", "BOOL"}:
        return item
    if kind == "NULL":
        return None
    raise SmokeFailure()


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _audit_safe(rows, forbidden, expected_traces):
    try:
        records = []
        for row in rows:
            if "record" in row:
                if set(row) != {"pk", "sk", "record"} or row["pk"] != {"S": "event"}:
                    return False
                row = _dynamo_value(row["record"])
            if (not isinstance(row, dict) or set(row)-_AUDIT_FIELDS
                    or not {"trace_id", "operation", "decision", "reason_code"} <= set(row)):
                return False
            records.append(row)
        if not records or not expected_traces <= {row["trace_id"] for row in records}:
            return False
        return not any(secret and secret in value for value in _strings(records) for secret in forbidden)
    except Exception:
        return False


class _Checks:
    def __init__(self, request, evidence):
        self.request = request
        self.evidence = evidence

    def record(self, name, passed, code=None, result=None):
        check = {"name": name, "status": "PASS" if passed else "FAIL"}
        if type(code) is int:
            check["http_status"] = code
        trace = result.get("trace_id") if isinstance(result, dict) else None
        if isinstance(trace, str) and _TRACE.fullmatch(trace):
            check["trace_id"] = trace
        self.evidence["checks"].append(check)
        print(" ".join([name, check["status"]]+[
            f"{key}={check[key]}" for key in ("http_status", "trace_id") if key in check
        ]))
        if not passed:
            raise SmokeFailure()

    def call(self, name, method, path, expected, token=None, body=None, key=None, predicate=None, timeout=30):
        try:
            code, result, headers = self.request(method, path, token=token, body=body, key=key, timeout=timeout)
            passed = code == expected and (predicate is None or bool(predicate(result)))
        except Exception:
            self.record(name, False)
        self.record(name, passed, code, result)
        return result

    def ready(self, name, memory_id, token):
        deadline = time.monotonic()+90
        while time.monotonic() < deadline:
            result = self.call(name+"_status", "GET", "/v1/memories/"+memory_id+"/status", 200,
                               token=token, timeout=min(30, max(0.1, deadline-time.monotonic())))
            state = result.get("state")
            if state == "APPROVED":
                self.record(name+"_approved", True, result=result)
                return
            if state not in {"PENDING", "INGESTING"}:
                self.record(name+"_approved", False, result=result)
            time.sleep(min(2, max(0, deadline-time.monotonic())))
        self.record(name+"_approved", False)

    def search_ready(self, memory_id, token, query, expected_text):
        deadline = time.monotonic()+30
        while time.monotonic() < deadline:
            result = self.call("senso_public_search_poll", "POST", "/v1/memories/search", 200,
                token=token, body={"query": query, "max_results": 5},
                timeout=min(30, max(0.1, deadline-time.monotonic())))
            if any(row.get("memory_id") == memory_id and row.get("text") == expected_text
                   for row in result.get("results", [])):
                self.record("senso_public_search", True, result=result)
                return
            time.sleep(min(3, max(0, deadline-time.monotonic())))
        self.record("senso_public_search", False)


def run_checks(request, admin_token, artifact_dir, *, senso=False, audit_loader=None):
    """Run named checks; persist only allowlisted evidence and separate credentials.

    Injecting request/audit_loader supports offline verification against the real
    gateway policy. Only HttpTransport and the AWS CLI loader perform live I/O.
    """
    artifact_dir = Path(artifact_dir)
    evidence = {"smoke_id": uuid4().hex, "started_at": datetime.now(UTC).isoformat(),
                "mode": "aws_senso" if senso else "aws_boundary", "passed": False, "checks": []}
    checks = _Checks(request, evidence)
    tokens, forbidden = {}, [admin_token, CANARY, "100 Example Lane", "Avery Example"]
    try:
        checks.call("authentication_required", "GET", "/v1/sources/"+PUBLIC_SOURCE, 401)
        for name, template, principal, source_ids in (
            ("alpha", "tpl-alpha", "eng-alpha", [PUBLIC_SOURCE, PRIVATE_SOURCE]),
            ("beta", "tpl-beta", "eng-beta", [PUBLIC_SOURCE]),
            ("publish", "tpl-beta-publish", "eng-beta", [PUBLIC_SOURCE]),
        ):
            run = checks.call("mint_"+name+"_run", "POST", "/v1/admin/runs", 200, token=admin_token,
                body={"template_id": template, "principal_id": principal},
                predicate=lambda result, wanted=source_ids: (
                    isinstance(result, dict) and isinstance(result.get("token"), str)
                    and _TOKEN.fullmatch(result["token"]) and isinstance(result.get("run_id"), str)
                    and _TRACE.fullmatch(result["run_id"]) and result.get("source_ids") == wanted))
            tokens[name] = {key: run[key] for key in ("token", "run_id", "source_ids")}
            forbidden.append(run["token"])
        _write_private(artifact_dir/"run-tokens.json", {"runs": tokens, "created_at": evidence["started_at"]})
        alpha, beta, publishing = (tokens[name]["token"] for name in ("alpha", "beta", "publish"))
        private = checks.call("alpha_source_allowed", "GET", "/v1/sources/"+PRIVATE_SOURCE, 200,
                              token=alpha, predicate=lambda result: bool(result.get("text")))
        forbidden.append(private["text"])
        checks.call("beta_private_source_denied", "GET", "/v1/sources/"+PRIVATE_SOURCE, 403, token=beta)
        public = checks.call("public_source_allowed", "GET", "/v1/sources/"+PUBLIC_SOURCE, 200,
                             token=publishing, predicate=lambda result: bool(result.get("text")))

        candidate = {"title": "Smoke credential quarantine", "text": "Synthetic credential "+CANARY}
        idempotency = uuid4().hex
        memory = checks.call("canary_quarantined", "POST", "/v1/memories", 202, token=alpha,
                             body=candidate, key=idempotency,
                             predicate=lambda result: result.get("state") == "QUARANTINED"
                                 and result.get("reason_codes") == ["SECRET_MATCH"])
        memory_path = "/v1/memories/"+memory["memory_id"]
        for name, token in (("alpha", alpha), ("beta", beta)):
            checks.call(name+"_quarantine_unreadable", "GET", memory_path, 404, token=token)
        checks.call("idempotency_reuses_candidate", "POST", "/v1/memories", 202, token=alpha,
                    body=candidate, key=idempotency,
                    predicate=lambda result: result.get("memory_id") == memory["memory_id"])
        checks.call("idempotency_rejects_changed_content", "POST", "/v1/memories", 409, token=alpha,
                    body={"title": candidate["title"], "text": "Different candidate"}, key=idempotency)
        query = "smoke-query-"+uuid4().hex
        forbidden.append(query)
        checks.call("quarantined_search_excluded", "POST", "/v1/memories/search", 200, token=beta,
            body={"query": query}, predicate=lambda result: isinstance(result.get("results"), list)
                and all(row.get("memory_id") != memory["memory_id"] for row in result["results"])
                and not any(secret in value for value in _strings(result)
                            for secret in (CANARY, private["text"], "100 Example Lane", "Avery Example")))

        report_body = {"title": "Smoke public note", "text": "<script>synthetic smoke marker</script>",
                       "source_ids": [PUBLIC_SOURCE]}
        for name, token in (("alpha", alpha), ("beta", beta)):
            checks.call(name+"_publication_denied", "POST", "/v1/reports", 403, token=token, body=report_body)
        report = checks.call("public_publication_allowed", "POST", "/v1/reports", 200,
                             token=publishing, body=report_body)
        checks.call("public_report_escaped", "GET", "/reports/"+report["report_id"], 200,
                    predicate=lambda result: isinstance(result, str) and "<script>" not in result
                        and "&lt;script&gt;" in result and CANARY not in result)
        checks.call("report_secret_denied", "POST", "/v1/reports", 400, token=publishing,
                    body={**report_body, "text": CANARY})

        if senso:
            excerpt = _public_excerpt(public["text"])
            public_text = excerpt+"\nSmoke reference "+evidence["smoke_id"]
            forbidden.append(excerpt[:100])
            published = checks.call("senso_public_ingest", "POST", "/v1/memories", 202,
                token=publishing, body={"title": "Smoke approved public knowledge", "text": public_text}, key=uuid4().hex,
                predicate=lambda result: result.get("state") in {"INGESTING", "APPROVED"})
            public_id = published["memory_id"]
            checks.ready("senso_public", public_id, publishing)
            checks.search_ready(public_id, beta, excerpt[:100], public_text)
            checks.call("senso_public_direct", "GET", "/v1/memories/"+public_id, 200, token=beta,
                        predicate=lambda result: result.get("text") == public_text)
            private_text = private["text"][:2000]+"\nSmoke reference "+evidence["smoke_id"]
            private_memory = checks.call("senso_private_ingest", "POST", "/v1/memories", 202, token=alpha,
                body={"title": "Smoke private knowledge", "text": private_text}, key=uuid4().hex,
                predicate=lambda result: result.get("state") in {"INGESTING", "APPROVED"})
            private_id = private_memory["memory_id"]
            checks.ready("senso_private", private_id, alpha)
            checks.call("senso_alpha_private_allowed", "GET", "/v1/memories/"+private_id, 200,
                        token=alpha, predicate=lambda result: result.get("text") == private_text)
            checks.call("senso_beta_private_denied", "GET", "/v1/memories/"+private_id, 404, token=beta)
            checks.call("senso_beta_search_excludes_private", "POST", "/v1/memories/search", 200, token=beta,
                body={"query": query}, predicate=lambda result: private_text not in json.dumps(result)
                    and all(row.get("memory_id") != private_id for row in result.get("results", [])))
            for name, mid, token in (("public", public_id, beta), ("private", private_id, alpha)):
                checks.call(name+"_revocation_committed", "POST", "/v1/admin/memories/"+mid+"/revoke", 200,
                            token=admin_token, body={"reason_code": "TEST"},
                            predicate=lambda result: result.get("state") == "REVOKED")
                checks.call(name+"_revocation_immediate", "GET", "/v1/memories/"+mid, 404, token=token)
        if audit_loader is not None:
            rows = audit_loader()
            traces = {check["trace_id"] for check in evidence["checks"] if "trace_id" in check}
            checks.record("audit_metadata_privacy", _audit_safe(rows, forbidden, traces))
        evidence["passed"] = True
    except SmokeFailure:
        pass
    except Exception:
        try:
            checks.record("smoke_dependency", False)
        except SmokeFailure:
            pass
    finally:
        _write_private(artifact_dir/"live-smoke.json", evidence)
    return evidence["passed"]


def load_audit_records(table, profile="default", region="us-east-1"):
    """Query only metadata events; AWS output and errors remain in memory."""
    completed = subprocess.run([
        "aws", "dynamodb", "query", "--table-name", table,
        "--key-condition-expression", "#pk = :kind",
        "--expression-attribute-names", '{"#pk":"pk"}',
        "--expression-attribute-values", '{":kind":{"S":"event"}}',
        "--consistent-read", "--profile", profile, "--region", region,
        "--output", "json", "--no-cli-pager",
    ], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(completed.stdout).get("Items", [])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run operator-only live gateway checks without printing private data")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--senso", action="store_true", help="Also verify real provider ingestion, retrieval, and revocation")
    parser.add_argument("--table", help="Optionally verify DynamoDB event metadata privacy with AWS CLI")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="us-east-1")
    arguments = parser.parse_args(argv)
    try:
        root = Path(__file__).resolve().parents[1]
        admin = json.loads((root/".gateway-admin.json").read_text())["admin_token"]
        if not isinstance(admin, str) or not _TOKEN.fullmatch(admin):
            raise SmokeFailure()
        audit_loader = None
        if arguments.table:
            audit_loader = lambda: load_audit_records(arguments.table, arguments.profile, arguments.region)
        passed = run_checks(HttpTransport(arguments.base_url), admin, root/"build",
                            senso=arguments.senso, audit_loader=audit_loader)
        return 0 if passed else 1
    except Exception:
        print("smoke_setup FAIL")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
