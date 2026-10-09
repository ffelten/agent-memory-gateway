"""Storage invariants run against both memory and an isolated DynamoDB emulator."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import boto3
import pytest
from moto import mock_aws


@pytest.fixture(params=["memory", "dynamo"])
def store(request):
    if request.param == "memory":
        from gateway.store import Store

        yield Store()
        return

    from gateway.dynamo import DynamoStore

    with mock_aws():
        resource = boto3.resource(
            "dynamodb",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        table = resource.create_table(
            TableName="gateway-test",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield DynamoStore(table)


def candidate(memory_id="memory-1", content_hash="hash-1"):
    return {
        "memory_id": memory_id,
        "content_hash": content_hash,
        "state": "PENDING",
        "text": "private test candidate",
        "source_ids": ["source-1"],
    }


def test_put_returns_revision_and_does_not_retain_mutable_inputs(store):
    value = {"state": "INGESTING", "source_ids": ["source-1"]}
    saved = store.put("memory", "memory-1", value, create_only=True)

    assert saved == {"state": "INGESTING", "source_ids": ["source-1"], "_rev": 1}
    assert "_rev" not in value
    value["source_ids"].clear()
    saved["source_ids"].append("source-2")
    assert store.get("memory", "memory-1")["source_ids"] == ["source-1"]


def test_get_and_list_do_not_allow_uncommitted_mutation(store):
    store.put("source", "source-1", {"readers": ["alpha"]})
    store.put("memory", "memory-1", {"readers": ["beta"]})
    direct = store.get("source", "source-1")
    direct["readers"].append("beta")
    listed = store.list("source")
    assert len(listed) == 1
    listed[0]["readers"].clear()

    assert store.get("source", "source-1")["readers"] == ["alpha"]
    assert store.get("source", "missing") is None
    assert store.list("missing") == []


def test_create_only_cannot_replace_existing_record(store):
    from gateway.store import Conflict

    store.put("source", "source-1", {"readers": ["alpha"]}, create_only=True)
    with pytest.raises(Conflict):
        store.put("source", "source-1", {"readers": ["*"]}, create_only=True)
    assert store.get("source", "source-1")["readers"] == ["alpha"]


def test_stale_approval_cannot_overwrite_committed_revocation(store):
    from gateway.store import Conflict

    pending = store.put("memory", "memory-1", {"state": "INGESTING"})
    revoked = store.put(
        "memory", "memory-1", {"state": "REVOKED"}, expected_revision=pending["_rev"]
    )
    assert revoked["_rev"] == 2
    with pytest.raises(Conflict):
        store.put(
            "memory", "memory-1", {"state": "APPROVED"},
            expected_revision=pending["_rev"],
        )
    assert store.get("memory", "memory-1") == {"state": "REVOKED", "_rev": 2}


def test_conditional_update_cannot_create_missing_record(store):
    from gateway.store import Conflict

    with pytest.raises(Conflict):
        store.put("memory", "missing", {"state": "APPROVED"}, expected_revision=1)
    assert store.get("memory", "missing") is None


def test_create_only_cannot_bypass_an_explicit_revision_requirement(store):
    from gateway.store import Conflict

    with pytest.raises(Conflict):
        store.put("memory", "missing", {"state": "APPROVED"}, expected_revision=1, create_only=True)
    assert store.get("memory", "missing") is None


def test_two_writers_with_one_revision_have_exactly_one_winner(store):
    from gateway.store import Conflict

    saved = store.put("source", "source-1", {"readers": ["alpha"]})
    barrier = Barrier(2)

    def replace(readers):
        barrier.wait()
        try:
            store.put("source", "source-1", {"readers": readers}, expected_revision=1)
            return True
        except Conflict:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(replace, [["beta"], []]))
    assert sorted(outcomes) == [False, True]
    assert store.get("source", "source-1")["_rev"] == saved["_rev"] + 1


def test_candidate_retry_returns_original_current_record(store):
    initial, created = store.create_candidate(candidate(), "scoped-idempotency-key")
    assert created is True
    assert initial["_rev"] == 1
    approved = deepcopy(initial)
    approved["state"] = "APPROVED"
    store.put("memory", initial["memory_id"], approved, expected_revision=1)

    retried, created = store.create_candidate(candidate("new-unused-id"), "scoped-idempotency-key")
    assert created is False
    assert retried["memory_id"] == "memory-1"
    assert retried["state"] == "APPROVED"
    assert retried["_rev"] == 2
    assert store.get("memory", "new-unused-id") is None
    assert len(store.list("memory")) == 1


def test_idempotency_key_rejects_changed_content_without_saving_it(store):
    from gateway.store import IdempotencyConflict

    store.create_candidate(candidate(), "scoped-idempotency-key")
    with pytest.raises(IdempotencyConflict):
        store.create_candidate(candidate("memory-2", "different-hash"), "scoped-idempotency-key")
    assert store.get("memory", "memory-2") is None
    assert store.get("memory", "memory-1")["content_hash"] == "hash-1"


def test_failed_candidate_creation_does_not_reserve_idempotency_key(store):
    from gateway.store import Conflict

    store.put("memory", "memory-1", candidate(), create_only=True)
    with pytest.raises(Conflict):
        store.create_candidate(candidate(), "available-key")

    saved, created = store.create_candidate(candidate("memory-2"), "available-key")
    assert created is True
    assert saved["memory_id"] == "memory-2"


@pytest.mark.parametrize("second_hash", ["hash-1", "different-hash"])
def test_simultaneous_candidates_keep_one_atomic_idempotency_binding(store, second_hash):
    from gateway.store import IdempotencyConflict

    barrier = Barrier(2)
    if hasattr(store, "table"):
        # Both reads must observe no reservation before either transaction runs.
        def align_transactions(**kwargs):
            barrier.wait(timeout=5)

        store.table.meta.client.meta.events.register(
            "before-call.dynamodb.TransactWriteItems", align_transactions
        )

    def create(value):
        if not hasattr(store, "table"):
            barrier.wait(timeout=5)
        try:
            return store.create_candidate(value, "same-scoped-key")
        except IdempotencyConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create, [candidate(), candidate("memory-2", second_hash)]))

    records = store.list("memory")
    assert len(records) == 1
    assert len(store.list("idempotency")) == 1
    assert sum(result is not None and result[1] for result in outcomes) == 1
    if second_hash == "hash-1":
        assert all(result[0]["memory_id"] == records[0]["memory_id"] for result in outcomes)
    else:
        assert sum(result is None for result in outcomes) == 1


def test_float_metrics_round_trip_as_plain_python_numbers(store):
    saved = store.put("outbox", "event-1", {"gate_ms": 1.25, "total_ms": 2.0})
    assert saved["gate_ms"] == 1.25
    loaded = store.get("outbox", "event-1")
    assert isinstance(loaded["gate_ms"], (int, float))
    assert loaded["gate_ms"] == 1.25
    assert loaded["total_ms"] == 2.0


def test_list_returns_all_records_across_dynamodb_page_boundary(store):
    for index in range(9):
        store.put("memory", str(index), {"memory_id": str(index), "text": "x" * 150_000})
    store.put("source", "source-1", {"text": "excluded"})

    records = store.list("memory")
    assert {record["memory_id"] for record in records} == {str(index) for index in range(9)}


def test_dynamodb_reads_are_strongly_consistent(store):
    if not hasattr(store, "table"):
        pytest.skip("DynamoDB request invariant")

    requests = []

    def inspect_request(params, model, **kwargs):
        if model.name in {"GetItem", "Query"}:
            requests.append(deepcopy(params))

    store.table.meta.client.meta.events.register("before-parameter-build.dynamodb", inspect_request)
    store.get("source", "missing")
    store.list("source")

    assert len(requests) == 2
    assert all(request["ConsistentRead"] is True for request in requests)
