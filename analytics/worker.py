"""At-least-once audit export. Failures leave the original event pending."""

import json
import os

from analytics.clickhouse import ClickHouseSink
from analytics.events import export_event


def flush_pending(store, sink, limit=100):
    """Insert a bounded batch, then conditionally acknowledge each saved row.

    A crash after insertion but before acknowledgement can produce duplicates.
    Consumers must deduplicate by event_id; retries never mint another ID.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("invalid audit batch limit")
    pending = [record for record in store.list("event") if record.get("delivered") is False][:limit]
    result = {"attempted": len(pending), "delivered": 0, "failed": 0}
    valid = []
    rows = []
    for record in pending:
        try:
            rows.append(export_event(record))
            valid.append(record)
        except (ValueError, TypeError):
            result["failed"] += 1
    if not rows:
        return result
    try:
        sink.insert(rows)
    except Exception:
        # Neither provider messages nor credentials belong in Lambda logs.
        result["failed"] += len(valid)
        return result
    for record in valid:
        try:
            store.put("event", record["event_id"], {**record, "delivered": True}, expected_revision=record["_rev"])
            result["delivered"] += 1
        except Exception:
            result["failed"] += 1
    return result


def lambda_handler(event, context):
    """Scheduled worker; configuration comes only from its trusted environment."""
    try:
        table_name = os.environ["GATEWAY_TABLE"]
        secret_arn = os.environ["CLICKHOUSE_SECRET_ARN"]
        import boto3
        from botocore.config import Config
        from gateway.dynamo import DynamoStore

        config = Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1})
        secret_value = boto3.client("secretsmanager", config=config).get_secret_value(SecretId=secret_arn)
        secret = json.loads(secret_value["SecretString"])
        if not isinstance(secret, dict) or set(secret) != {"url", "username", "password", "database"}:
            raise ValueError("invalid audit sink configuration")
        sink = ClickHouseSink(**secret)
        table = boto3.resource("dynamodb", config=config).Table(table_name)
        return flush_pending(DynamoStore(table), sink)
    except Exception:
        # A failed invocation never modifies pending records before confirmed
        # delivery. The next scheduled invocation retries with the same IDs.
        return {"reason_code": "DEPENDENCY_UNAVAILABLE"}
