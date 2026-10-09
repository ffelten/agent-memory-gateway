"""DynamoDB storage with consistent reads and conditional, atomic writes."""

from copy import deepcopy
from decimal import Decimal

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from gateway.store import Conflict, IdempotencyConflict


def _to_dynamo(value):
    """Keep JSON-style records compatible with DynamoDB numeric encoding."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _to_dynamo(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_dynamo(item) for item in value]
    return value


def _from_dynamo(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _from_dynamo(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(item) for item in value]
    return deepcopy(value)


class DynamoStore:
    """Store records in a boto3 Table keyed by string ``pk``/``sk``.

    ``pk`` is the record kind and ``sk`` is its application key. A paginated
    Query, rather than a table scan, lets the analytics worker's IAM role read
    only its event partition. Every authorization decision still needs get():
    a multi-page list is not a transactional snapshot.
    """

    def __init__(self, table):
        self.table = table

    def get(self, kind, key):
        response = self.table.get_item(
            Key={"pk": kind, "sk": key}, ConsistentRead=True,
        )
        item = response.get("Item")
        return _from_dynamo(item["record"]) if item is not None else None

    def put(self, kind, key, value, expected_revision=None, create_only=False):
        if create_only and expected_revision is not None:
            raise Conflict("Incompatible write conditions")
        if create_only:
            revision = None
        elif expected_revision is not None:
            revision = expected_revision
        else:
            current = self.get(kind, key)
            revision = current["_rev"] if current is not None else None

        saved = deepcopy(value)
        saved["_rev"] = revision + 1 if revision is not None else 1
        request = {"Item": {"pk": kind, "sk": key, "record": _to_dynamo(saved)}}
        if revision is None:
            request.update(
                ConditionExpression="attribute_not_exists(#pk)",
                ExpressionAttributeNames={"#pk": "pk"},
            )
        else:
            request.update(
                ConditionExpression="#record.#rev = :expected",
                ExpressionAttributeNames={"#record": "record", "#rev": "_rev"},
                ExpressionAttributeValues={":expected": revision},
            )

        try:
            self.table.put_item(**request)
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise Conflict("Record revision changed") from None
            raise
        return saved

    def list(self, kind):
        records = []
        request = {
            "KeyConditionExpression": Key("pk").eq(kind),
            "ConsistentRead": True,
        }
        while True:
            response = self.table.query(**request)
            records.extend(_from_dynamo(item["record"]) for item in response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return records
            request["ExclusiveStartKey"] = last_key

    def _existing_candidate(self, memory, idempotency_id):
        existing = self.get("idempotency", idempotency_id)
        if existing is None:
            return None
        if existing["content_hash"] != memory["content_hash"]:
            raise IdempotencyConflict("Idempotency key already used")
        record = self.get("memory", existing["memory_id"])
        if record is None:
            raise Conflict("Candidate record unavailable")
        return record

    def create_candidate(self, memory, idempotency_id):
        existing = self._existing_candidate(memory, idempotency_id)
        if existing is not None:
            return existing, False

        saved = deepcopy(memory)
        saved["_rev"] = 1
        idempotency = {
            "memory_id": saved["memory_id"],
            "content_hash": saved["content_hash"],
            "_rev": 1,
        }
        records = [
            ("memory", saved["memory_id"], saved),
            ("idempotency", idempotency_id, idempotency),
        ]
        transaction = [
            {
                "Put": {
                    "TableName": self.table.name,
                    "Item": {"pk": kind, "sk": key, "record": _to_dynamo(record)},
                    "ConditionExpression": "attribute_not_exists(#pk)",
                    "ExpressionAttributeNames": {"#pk": "pk"},
                }
            }
            for kind, key, record in records
        ]
        try:
            # The resource's client applies its native Python-value serializer.
            self.table.meta.client.transact_write_items(TransactItems=transaction)
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            reasons = error.response.get("CancellationReasons", [])
            if not any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons):
                raise
            existing = self._existing_candidate(memory, idempotency_id)
            if existing is not None:
                return existing, False
            raise Conflict("Candidate reservation conflict") from None
        return saved, True
