"""Authoritative record-store contract and an in-memory implementation for tests."""

from copy import deepcopy
from threading import RLock


class Conflict(Exception):
    """The record changed, or an exclusive create found an existing record."""


class IdempotencyConflict(Conflict):
    """An idempotency key is already bound to different candidate content."""


class InMemoryStore:
    """Thread-safe local store with the same revision contract as DynamoStore.

    Updates based on a prior read must supply its ``_rev`` as
    ``expected_revision``. Unqualified puts replace the current record and are
    intended for trusted initialization, not state transitions.
    """

    def __init__(self):
        self._records = {}
        self._lock = RLock()

    def get(self, kind, key):
        with self._lock:
            return deepcopy(self._records.get((kind, key)))

    def put(self, kind, key, value, expected_revision=None, create_only=False):
        with self._lock:
            current = self._records.get((kind, key))
            if create_only and current is not None:
                raise Conflict("Record already exists")
            if expected_revision is not None and (
                current is None or current["_rev"] != expected_revision
            ):
                raise Conflict("Record revision changed")
            saved = deepcopy(value)
            saved["_rev"] = current["_rev"] + 1 if current is not None else 1
            self._records[(kind, key)] = saved
            return deepcopy(saved)

    def list(self, kind):
        with self._lock:
            return [
                deepcopy(record)
                for (record_kind, _), record in self._records.items()
                if record_kind == kind
            ]

    def create_candidate(self, memory, idempotency_id):
        with self._lock:
            existing = self._records.get(("idempotency", idempotency_id))
            if existing is not None:
                if existing["content_hash"] != memory["content_hash"]:
                    raise IdempotencyConflict("Idempotency key already used")
                record = self._records.get(("memory", existing["memory_id"]))
                if record is None:
                    raise Conflict("Candidate record unavailable")
                return deepcopy(record), False

            memory_key = ("memory", memory["memory_id"])
            if memory_key in self._records:
                raise Conflict("Record already exists")
            saved = deepcopy(memory)
            saved["_rev"] = 1
            self._records[memory_key] = saved
            self._records[("idempotency", idempotency_id)] = {
                "memory_id": memory["memory_id"],
                "content_hash": memory["content_hash"],
                "_rev": 1,
            }
            return deepcopy(saved), True


# The shared contract originally named the local implementation Store.
Store = InMemoryStore
