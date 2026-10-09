"""Lambda wiring checks against local AWS emulation and injected HTTPS I/O."""

import json

import boto3
from moto import mock_aws


def test_lambda_loads_managed_secret_and_exports_only_event_partition(monkeypatch, metadata):
    from analytics import worker
    from analytics.clickhouse import ClickHouseSink
    from analytics.events import record_event
    from gateway.dynamo import DynamoStore

    class Transport:
        data = None

        def open(self, request, timeout):
            self.data = request.data

            class Response:
                status = 200
                headers = {}

                def read(self, size):
                    return b""

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return None

            return Response()

    transport = Transport()
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        table = boto3.resource("dynamodb").create_table(
            TableName="audit-runtime-test",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        store = DynamoStore(table)
        event = record_event(store, **metadata)
        store.put("memory", "private", {"text": "DEMO_SECRET_ALPHA_2026 100 Example Lane"})
        secret = boto3.client("secretsmanager").create_secret(
            Name="clickhouse-test",
            SecretString=json.dumps({"url": "https://clickhouse.example.test/", "username": "writer", "password": "provider-private-password", "database": "default"}),
        )
        monkeypatch.setenv("GATEWAY_TABLE", table.name)
        monkeypatch.setenv("CLICKHOUSE_SECRET_ARN", secret["ARN"])
        monkeypatch.setattr(worker, "ClickHouseSink", lambda **config: ClickHouseSink(**config, opener=transport))
        result = worker.lambda_handler({"url": "http://untrusted.invalid/"}, None)
        assert result == {"attempted": 1, "delivered": 1, "failed": 0}
        assert store.get("event", event["event_id"])["delivered"] is True
        assert b"DEMO_SECRET_ALPHA_2026" not in transport.data
        assert b"100 Example Lane" not in transport.data
        assert b"provider-private-password" not in transport.data


def test_lambda_configuration_failure_is_bounded_without_raw_error(monkeypatch, caplog, capsys):
    from analytics.worker import lambda_handler

    monkeypatch.delenv("GATEWAY_TABLE", raising=False)
    monkeypatch.delenv("CLICKHOUSE_SECRET_ARN", raising=False)
    assert lambda_handler({"token": "DEMO_SECRET_ALPHA_2026"}, None) == {"reason_code": "DEPENDENCY_UNAVAILABLE"}
    assert "DEMO_SECRET_ALPHA_2026" not in caplog.text + str(capsys.readouterr())
