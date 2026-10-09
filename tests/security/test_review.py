"""Adversarial regressions for the trusted policy boundary."""

import json

import pytest

from gateway.provider import Passage
from gateway.scanner import has_secret
from tests.security.test_policy import approve, create, rig


@pytest.mark.parametrize("invalid_result", [None, 0, ""])
def test_publication_rejects_invalid_scanner_results(rig, invalid_result):
    rig[0].scanner = lambda *args: invalid_result
    status, body, _ = rig[3](
        "POST", "/v1/reports", rig[4]["pub"],
        {"title": "Note", "text": "DEMO_SECRET_ALPHA_2026", "source_ids": ["src-public-runbook"]},
    )
    assert status == 503
    assert body["reason_codes"] == ["SCANNER_ERROR"]
    assert rig[1].list("report") == []


def test_search_returns_registered_text_instead_of_mislabeled_provider_bytes(rig):
    memory_id = approve(rig, who="pub", text="Approved public procedure")
    memory = rig[1].get("memory", memory_id)
    rig[2].search = lambda **kwargs: [
        Passage(memory["content_id"], "Avery Example, 100 Example Lane, Service Plan A.", "1")
    ]

    status, body, _ = rig[3]("POST", "/v1/memories/search", rig[4]["beta"], {"query": "procedure"})

    assert status == 200
    assert [result["text"] for result in body["results"]] == ["Approved public procedure"]
    assert "100 Example Lane" not in json.dumps(body)


def test_search_rejects_invalid_scanner_result(rig):
    approve(rig, who="pub", text="Approved public procedure")
    rig[0].scanner = lambda *args: None

    status, body, _ = rig[3]("POST", "/v1/memories/search", rig[4]["beta"], {"query": "procedure"})

    assert status == 503
    assert body["reason_codes"] == ["SCANNER_ERROR"]
    assert "Approved public procedure" not in json.dumps(body)


@pytest.mark.parametrize("operation", ["report", "search"])
def test_scanner_timeout_prevents_publication_and_disclosure(rig, monkeypatch, operation):
    if operation == "search":
        approve(rig, who="pub", text="Approved public procedure")
    clock = [10.0]
    monkeypatch.setattr("gateway.service.time.monotonic", lambda: clock[0])
    def delayed_scanner(title, text):
        clock[0] += 0.3
        return has_secret(title, text)
    rig[0].scanner = delayed_scanner

    if operation == "report":
        status, body, _ = rig[3](
            "POST", "/v1/reports", rig[4]["pub"],
            {"title": "Note", "text": "Approved public procedure", "source_ids": ["src-public-runbook"]},
        )
        assert rig[1].list("report") == []
    else:
        status, body, _ = rig[3]("POST", "/v1/memories/search", rig[4]["beta"], {"query": "procedure"})
    assert status == 503
    assert body["reason_codes"] == ["TIMEOUT"]


def test_search_stops_provider_checks_when_request_budget_is_exhausted(rig, monkeypatch):
    for index in range(5):
        approve(rig, who="pub", text="Approved public procedure", key=str(index))
    clock = [10.0]
    monkeypatch.setattr("gateway.service.time.monotonic", lambda: clock[0])
    original = rig[2].inspect
    inspected = []
    def slow_inspect(node_id):
        inspected.append(node_id)
        clock[0] += 4.0
        return original(node_id)
    rig[2].inspect = slow_inspect

    status, body, _ = rig[3]("POST", "/v1/memories/search", rig[4]["beta"], {"query": "procedure"})

    assert status == 503
    assert body["reason_codes"] == ["TIMEOUT"]
    assert len(inspected) <= 4
    assert not any(call[0] == "search" for call in rig[2].calls)


def test_search_fails_closed_if_demo_candidate_limit_is_exceeded(rig):
    for index in range(101):
        rig[1].put("memory", str(index), {"memory_id": str(index), "state": "PENDING"})

    status, body, _ = rig[3]("POST", "/v1/memories/search", rig[4]["beta"], {"query": "procedure"})

    assert status == 503
    assert body["error"] == "dependency_unavailable"
    assert not rig[2].calls


def test_revocation_during_ingestion_keeps_returned_node_queued_for_deletion(rig):
    original = rig[2].ingest
    def ingest_and_revoke(**kwargs):
        document = original(**kwargs)
        status, _, _ = rig[3](
            "POST", "/v1/admin/memories/"+kwargs["external_id"]+"/revoke",
            "admin-for-tests", {"reason_code": "REVOKED"},
        )
        assert status == 200
        return document
    rig[2].ingest = ingest_and_revoke

    create(rig, who="pub")

    memory = rig[1].list("memory")[0]
    assert memory["state"] == "REVOKED"
    assert memory["delete_pending"] is True
    assert memory["node_id"] in rig[2].docs
    assert rig[3]("GET", "/v1/memories/"+memory["memory_id"], rig[4]["beta"])[0] == 404


@pytest.mark.parametrize("operation", ["memory", "search", "source", "report"])
def test_audit_persistence_failure_blocks_protected_response(rig, operation):
    memory_id = approve(rig, who="pub", text="Approved public procedure")
    status, published, _ = rig[3](
        "POST", "/v1/reports", rig[4]["pub"],
        {"title": "Note", "text": "Approved public procedure", "source_ids": ["src-public-runbook"]},
    )
    assert status == 200
    original = rig[1].put
    def failed_audit(kind, *args, **kwargs):
        if kind == "event":
            raise RuntimeError("private provider detail must not leak")
        return original(kind, *args, **kwargs)
    rig[1].put = failed_audit

    if operation == "memory":
        result = rig[3]("GET", "/v1/memories/"+memory_id, rig[4]["beta"])
    elif operation == "search":
        result = rig[3]("POST", "/v1/memories/search", rig[4]["beta"], {"query": "procedure"})
    elif operation == "source":
        result = rig[3]("GET", "/v1/sources/src-public-runbook", rig[4]["beta"])
    else:
        result = rig[3]("GET", "/reports/"+published["report_id"])
    assert result[0] == 503
    assert result[1]["error"] == "audit_unavailable"
    assert "Approved public procedure" not in json.dumps(result)
    assert "private provider detail" not in json.dumps(result)


@pytest.mark.parametrize("text", [
    '{"api_key":"sk_live_abcdefghijk"}',
    "{'password': 'long_private_value'}",
    '{"token": "opaque_credential_value"}',
])
def test_quoted_credential_fields_are_quarantined_before_ingestion(rig, text):
    status, body, _ = create(rig, text=text)

    assert status == 202
    assert body["state"] == "QUARANTINED"
    assert body["reason_codes"] == ["SECRET_MATCH"]
    assert not rig[2].calls


def test_retry_while_original_ingestion_runs_does_not_ingest_twice(rig):
    original = rig[2].ingest
    retries = []
    def ingest_with_retry(**kwargs):
        retries.append(create(rig, who="pub"))
        return original(**kwargs)
    rig[2].ingest = ingest_with_retry

    status, body, _ = create(rig, who="pub")

    assert status == 202
    assert retries[0][0] == 202
    assert retries[0][1]["memory_id"] == body["memory_id"]
    assert [call[0] for call in rig[2].calls] == ["ingest"]


def test_permission_narrowed_during_search_removes_selected_memory(rig):
    approve(rig, who="pub", text="Approved public procedure")
    def remove_beta_permission():
        source = rig[1].get("source", "src-public-runbook")
        rig[1].put("source", source["source_id"], dict(source, readers=["eng-alpha"]),
                   expected_revision=source["_rev"])
    rig[2].on_search = remove_beta_permission

    status, body, _ = rig[3]("POST", "/v1/memories/search", rig[4]["beta"], {"query": "procedure"})

    assert status == 200
    assert body["results"] == []


def test_report_tracks_uncited_sources_from_full_publishing_context(rig):
    source = rig[1].get("source", "src-public-runbook")
    rig[1].put("source", "src-second-public", dict(source, source_id="src-second-public"))
    template = rig[1].get("template", "tpl-beta-publish")
    rig[1].put("template", template["template_id"],
               dict(template, source_ids=["src-public-runbook", "src-second-public"]),
               expected_revision=template["_rev"])
    status, run, _ = rig[3](
        "POST", "/v1/admin/runs", "admin-for-tests",
        {"template_id": "tpl-beta-publish", "principal_id": "eng-beta"},
    )
    assert status == 200
    status, report, _ = rig[3](
        "POST", "/v1/reports", run["token"],
        {"title": "Note", "text": "Information learned from both sources", "source_ids": ["src-public-runbook"]},
    )
    assert status == 200
    source = rig[1].get("source", "src-second-public")
    rig[1].put("source", source["source_id"], dict(source, public=False), expected_revision=source["_rev"])

    status, body, _ = rig[3]("GET", "/reports/"+report["report_id"])

    assert status == 404
    assert "Information learned" not in json.dumps(body)
