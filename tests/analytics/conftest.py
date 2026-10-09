from copy import deepcopy
from uuid import uuid4

import pytest


class MemoryStore:
    """Store-contract double, including optimistic acknowledgement failures."""

    def __init__(self):
        self.records = {}
        self.fail_ack = False

    def get(self, kind, key):
        return deepcopy(self.records.get((kind, key)))

    def put(self, kind, key, value, expected_revision=None, create_only=False):
        previous = self.records.get((kind, key))
        if create_only and previous is not None:
            raise RuntimeError("conflict")
        if expected_revision is not None:
            if self.fail_ack or previous is None or previous["_rev"] != expected_revision:
                raise RuntimeError("conflict")
        saved = deepcopy(value)
        saved["_rev"] = 1 if previous is None else previous["_rev"] + 1
        self.records[kind, key] = saved
        return deepcopy(saved)

    def list(self, kind):
        return [deepcopy(v) for (k, _), v in self.records.items() if k == kind]


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def metadata():
    return {
        "trace_id": uuid4().hex,
        "tenant_id": "tenant-demo",
        "principal_id": "eng-alpha",
        "run_id": uuid4().hex,
        "memory_id": uuid4().hex,
        "application_version": 1,
        "operation": "create_memory",
        "transport": "http",
        "decision": "QUARANTINE",
        "reason_code": "SECRET_MATCH",
        "gate_ms": 1.25,
        "senso_ms": 0,
        "total_ms": 2.5,
    }
