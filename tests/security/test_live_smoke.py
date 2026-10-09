import json
import stat

from tests.security.test_policy import approve, rig


def transport(rig):
    def request(method, path, token=None, body=None, key=None, timeout=30):
        return rig[3](method, path, token, body, key)
    return request


def test_offline_boundary_smoke_preserves_credentials_and_redacts_evidence(rig, tmp_path, capsys):
    from gateway.live_smoke import run_checks

    result = run_checks(transport(rig), "admin-for-tests", tmp_path)

    assert result is True
    evidence = (tmp_path/"live-smoke.json").read_text()
    credentials = json.loads((tmp_path/"run-tokens.json").read_text())
    output = capsys.readouterr().out
    assert set(credentials["runs"]) == {"alpha", "beta", "publish"}
    for private in ["admin-for-tests", "DEMO_SECRET_ALPHA_2026", "100 Example Lane"]:
        assert private not in evidence+output
    for run in credentials["runs"].values():
        assert run["token"] not in evidence+output
    assert stat.S_IMODE((tmp_path/"run-tokens.json").stat().st_mode) == 0o600
    assert json.loads(evidence)["passed"] is True


def test_offline_full_lifecycle_retrieves_then_revokes_both_memories(rig, tmp_path):
    from gateway.live_smoke import run_checks

    assert run_checks(transport(rig), "admin-for-tests", tmp_path, senso=True) is True
    memories = rig[1].list("memory")
    assert len([memory for memory in memories if memory["state"] == "REVOKED"]) == 2
    evidence = json.loads((tmp_path/"live-smoke.json").read_text())
    assert any(check["name"] == "senso_public_search" and check["status"] == "PASS" for check in evidence["checks"])


def test_failed_check_records_only_sanitized_metadata(rig, tmp_path, capsys):
    from gateway.live_smoke import run_checks

    def untrusted_response(*args, **kwargs):
        return 503, {"error": "DEMO_SECRET_ALPHA_2026", "trace_id": "private-token-value"}, {}

    assert run_checks(untrusted_response, "admin-for-tests", tmp_path) is False
    evidence = (tmp_path/"live-smoke.json").read_text()
    output = capsys.readouterr().out
    assert "DEMO_SECRET" not in evidence+output
    assert "private-token-value" not in evidence+output
    assert json.loads(evidence)["passed"] is False


def test_audit_privacy_check_detects_secret_values_without_echoing_them(rig, tmp_path, capsys):
    from gateway.live_smoke import run_checks

    assert run_checks(transport(rig), "admin-for-tests", tmp_path,
                      audit_loader=lambda: [{"text": "DEMO_SECRET_ALPHA_2026"}]) is False
    evidence = (tmp_path/"live-smoke.json").read_text()
    assert "DEMO_SECRET_ALPHA_2026" not in evidence+capsys.readouterr().out
    assert json.loads(evidence)["checks"][-1]["name"] == "audit_metadata_privacy"


def test_audit_check_accepts_gateway_metadata_and_requires_matching_trace_ids(rig, tmp_path):
    from gateway.live_smoke import run_checks

    assert run_checks(transport(rig), "admin-for-tests", tmp_path,
                      audit_loader=lambda: rig[1].list("event")) is True


def test_audit_check_rejects_unapproved_fields_even_without_known_secret(rig, tmp_path):
    from gateway.live_smoke import run_checks

    def raw_event():
        return [dict(row, provider_error="otherwise unknown private response") for row in rig[1].list("event")]
    assert run_checks(transport(rig), "admin-for-tests", tmp_path, audit_loader=raw_event) is False


def test_audit_check_rejects_unrelated_or_missing_trace_records(rig, tmp_path):
    from gateway.live_smoke import run_checks

    original = rig[1].list("event")
    assert run_checks(transport(rig), "admin-for-tests", tmp_path, audit_loader=lambda: original) is False


def test_quarantine_check_allows_existing_approved_public_memories(rig, tmp_path):
    from gateway.live_smoke import run_checks

    approve(rig, who="pub", text="Previously approved public procedure")
    assert run_checks(transport(rig), "admin-for-tests", tmp_path) is True


def test_full_smoke_retries_public_search_until_provider_index_catches_up(rig, tmp_path, monkeypatch):
    from gateway.live_smoke import run_checks

    original = rig[2].search
    searches = []
    def delayed_search(**kwargs):
        searches.append(kwargs["query"])
        if len(searches) < 3:
            return []
        return original(**kwargs)
    rig[2].search = delayed_search
    monkeypatch.setattr("gateway.live_smoke.time.sleep", lambda _: None)

    assert run_checks(transport(rig), "admin-for-tests", tmp_path, senso=True) is True
    assert len(searches) >= 3


def test_full_smoke_candidates_are_unique_across_runs(rig, tmp_path):
    from gateway.live_smoke import run_checks

    assert run_checks(transport(rig), "admin-for-tests", tmp_path, senso=True) is True
    first_texts = {memory["text"] for memory in rig[1].list("memory") if memory["state"] == "REVOKED"}
    assert run_checks(transport(rig), "admin-for-tests", tmp_path, senso=True) is True
    revoked = [memory for memory in rig[1].list("memory") if memory["state"] == "REVOKED"]
    assert len(revoked) == 4
    assert len({memory["text"] for memory in revoked}-first_texts) == 2


def test_public_search_timeout_never_claims_provider_retrieval_success(rig, tmp_path, monkeypatch):
    from gateway.live_smoke import run_checks

    clock = [10.0]
    monkeypatch.setattr("gateway.live_smoke.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("gateway.live_smoke.time.sleep", lambda seconds: clock.__setitem__(0, clock[0]+seconds))
    rig[2].search = lambda **kwargs: []

    assert run_checks(transport(rig), "admin-for-tests", tmp_path, senso=True) is False
    evidence = json.loads((tmp_path/"live-smoke.json").read_text())
    assert evidence["checks"][-1] == {"name": "senso_public_search", "status": "FAIL"}
    assert clock[0] == 40.0


def test_audit_check_accepts_actual_dynamodb_attribute_encoding(rig, tmp_path):
    from boto3.dynamodb.types import TypeSerializer
    from gateway.live_smoke import run_checks

    def wire_records():
        serializer = TypeSerializer()
        return [{"pk": {"S": "event"}, "sk": {"S": row["event_id"]},
                 "record": serializer.serialize({key: str(value) if isinstance(value, float) else value
                                                  for key, value in row.items()})}
                for row in rig[1].list("event")]

    assert run_checks(transport(rig), "admin-for-tests", tmp_path, audit_loader=wire_records) is True
