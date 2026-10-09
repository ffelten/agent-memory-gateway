"""Closed audit schema: never accept request bodies or provider messages."""

from datetime import UTC, datetime
from decimal import Decimal
import math
from uuid import UUID, uuid4


POLICY_VERSION = "v1"
OPERATIONS = frozenset({
    "create_memory", "search", "get_memory", "get_status", "get_source",
    "create_report", "get_report", "create_run", "revoke",
})
DECISIONS = frozenset({"ALLOW", "DENY", "ERROR", "QUARANTINE"})
REASONS = frozenset({
    "OK", "SECRET_MATCH", "PROVENANCE_UNKNOWN", "SCANNER_ERROR",
    "SOURCE_ACCESS_DENIED", "PROVIDER_UNAVAILABLE", "PROVIDER_VERSION_CHANGED",
    "TIMEOUT", "NOT_READABLE", "INVALID_REQUEST", "UNAUTHORIZED", "REVOKED",
    "DEPENDENCY_UNAVAILABLE",
})
ENUMS = {
    "operation": OPERATIONS,
    "transport": frozenset({"http", "mcp"}),
    "decision": DECISIONS,
    "reason_code": REASONS,
}
IDENTITIES = ("trace_id", "tenant_id", "principal_id", "run_id", "memory_id")
TIMINGS = ("gate_ms", "senso_ms", "total_ms")
FIXTURE_IDS = {
    "tenant_id": frozenset({"tenant-demo"}),
    "principal_id": frozenset({"eng-alpha", "eng-beta"}),
}
FIELDS = frozenset((*IDENTITIES, *TIMINGS, *ENUMS, "application_version"))
COLUMNS = (
    "event_id", "trace_id", "tenant_id", "principal_id", "run_id", "memory_id",
    "application_version", "operation", "transport", "decision", "reason_code",
    "policy_version", "gate_ms", "senso_ms", "total_ms", "timestamp",
)


def _invalid():
    # Values and unknown key names can themselves be private request content.
    return ValueError("invalid audit metadata")


def _opaque_id(value, field):
    if type(value) is not str:
        raise _invalid()
    if value in FIXTURE_IDS.get(field, ()):
        return value
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise _invalid() from None
    if value not in (parsed.hex, str(parsed)):
        raise _invalid()
    return value


def _metadata(fields):
    if set(fields) - FIELDS:
        raise _invalid()
    result = {}
    for field, options in ENUMS.items():
        value = fields.get(field)
        if type(value) is not str or value not in options:
            raise _invalid()
        result[field] = value
    for field in IDENTITIES:
        value = fields.get(field)
        if value is None and field != "trace_id":
            result[field] = None
        else:
            result[field] = _opaque_id(value, field)
    version = fields.get("application_version")
    if version is not None:
        if not isinstance(version, (int, Decimal)) or isinstance(version, bool):
            raise _invalid()
        if not 1 <= version <= 2**32 - 1 or int(version) != version:
            raise _invalid()
        version = int(version)
    result["application_version"] = version
    for field in TIMINGS:
        value = fields.get(field, 0)
        if not isinstance(value, (int, float, Decimal)) or isinstance(value, bool):
            raise _invalid()
        try:
            value = float(value)
        except (OverflowError, ValueError):
            raise _invalid() from None
        if not math.isfinite(value) or value < 0:
            raise _invalid()
        result[field] = value
    return result


def record_event(store, **fields):
    """Validate trusted metadata and durably save one undelivered event.

    Storage failures deliberately propagate: the caller decides how to fail
    closed when its audit record could not be persisted.
    """
    event = _metadata(fields)
    event.update(
        event_id=uuid4().hex,
        timestamp=datetime.now(UTC).isoformat(timespec="milliseconds"),
        policy_version=POLICY_VERSION,
        delivered=False,
    )
    return store.put("event", event["event_id"], event, create_only=True)


def export_event(record):
    """Revalidate persisted rows; no envelope or unexpected field is exported."""
    if set(record) - set(COLUMNS) - {"delivered", "_rev"}:
        raise _invalid()
    event = _metadata({key: record[key] for key in FIELDS if key in record})
    event["event_id"] = _opaque_id(record.get("event_id"), "event_id")
    if record.get("policy_version") != POLICY_VERSION:
        raise _invalid()
    event["policy_version"] = POLICY_VERSION
    timestamp = record.get("timestamp")
    if type(timestamp) is not str:
        raise _invalid()
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        raise _invalid() from None
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise _invalid()
    # Normalize so even a liberal datetime parser cannot export arbitrary bytes.
    event["timestamp"] = parsed.isoformat(timespec="milliseconds")
    return {column: event[column] for column in COLUMNS}
