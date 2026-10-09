"""Privacy boundary tests: payloads must not become metadata."""

import json
from datetime import datetime
from uuid import UUID

import pytest


def test_record_event_durably_saves_server_generated_identity_and_time(store, metadata):
    from analytics.events import record_event

    event = record_event(store, **metadata)
    UUID(event["event_id"])
    assert datetime.fromisoformat(event["timestamp"]).utcoffset().total_seconds() == 0
    assert event["policy_version"]
    assert event["delivered"] is False
    assert store.get("event", event["event_id"]) == event
    assert {key: event[key] for key in metadata} == metadata


@pytest.mark.parametrize("field", ["text", "query", "token", "prompt", "error", "source_url", "event_id", "timestamp", "policy_version", "delivered"])
def test_rejects_payload_and_caller_controlled_envelope_fields(store, metadata, field):
    from analytics.events import record_event

    forbidden = "DEMO_SECRET_ALPHA_2026 Bearer hidden 100 Example Lane"
    with pytest.raises(ValueError) as raised:
        record_event(store, **metadata, **{field: forbidden})
    assert forbidden not in str(raised.value)
    assert store.list("event") == []


@pytest.mark.parametrize("forbidden", ["DEMO_SECRET_ALPHA_2026", "Bearer eyJhbGciOiJIUzI1NiJ9", "100 Example Lane", "where is my customer credential?", "ignore previous instructions", "raw_prompt_without_spaces"])
@pytest.mark.parametrize("field", ["trace_id", "tenant_id", "principal_id", "run_id", "memory_id", "operation", "transport", "decision", "reason_code"])
def test_safe_keys_cannot_smuggle_raw_strings(store, metadata, field, forbidden):
    from analytics.events import record_event

    metadata[field] = forbidden
    with pytest.raises(ValueError) as raised:
        record_event(store, **metadata)
    assert forbidden not in str(raised.value)
    assert store.list("event") == []


@pytest.mark.parametrize("field,value", [("gate_ms", float("nan")), ("senso_ms", float("inf")), ("total_ms", -1), ("gate_ms", True), ("application_version", "raw query"), ("application_version", True), ("application_version", 0)])
def test_numeric_fields_cannot_carry_invalid_values(store, metadata, field, value):
    from analytics.events import record_event

    metadata[field] = value
    with pytest.raises(ValueError):
        record_event(store, **metadata)
    assert store.list("event") == []


def test_anonymous_denial_has_no_fabricated_identity(store, metadata):
    from analytics.events import record_event

    for name in ("tenant_id", "principal_id", "run_id", "memory_id", "application_version"):
        metadata[name] = None
    metadata.update(operation="search", decision="DENY", reason_code="UNAUTHORIZED")
    event = record_event(store, **metadata)
    assert event["principal_id"] is None
    assert "UNAUTHORIZED" in json.dumps(event)
