"""Network contract tests use an injected HTTPS transport, never a live sponsor."""

from email.message import Message
from io import BytesIO
import json
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPSHandler
from urllib.response import addinfourl

import pytest


class Response:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self.body = BytesIO(body)
        self.headers = headers or {}
        self.read_sizes = []

    def read(self, size):
        self.read_sizes.append(size)
        return self.body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.body.close()


class Opener:
    def __init__(self, response=None, error=None):
        self.response = response or Response()
        self.error = error
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


def sink(opener=None, **overrides):
    from analytics.clickhouse import ClickHouseSink

    config = {
        "url": "https://clickhouse.example.test:8443/",
        "username": "audit_writer",
        "password": "private-provider-password",
        "database": "gateway_demo",
    }
    config.update(overrides)
    return ClickHouseSink(**config, opener=opener)


def test_insert_uses_named_columns_json_each_row_and_waits_for_final_result(store, metadata):
    from analytics.events import record_event
    from analytics.worker import flush_pending

    record_event(store, **metadata)
    transport = Opener()
    assert flush_pending(store, sink(transport))["delivered"] == 1
    request, timeout = transport.calls[0]
    assert request.method == "POST"
    assert 0 < timeout <= 10
    params = parse_qs(urlsplit(request.full_url).query)
    assert params["database"] == ["gateway_demo"]
    assert params["async_insert"] == ["0"]
    assert params["wait_end_of_query"] == ["1"]
    query = params["query"][0]
    assert query.startswith("INSERT INTO gateway_events (event_id, trace_id,")
    assert query.endswith("timestamp) FORMAT JSONEachRow")
    assert request.has_header("Authorization")
    assert "private-provider-password" not in request.full_url
    row = json.loads(request.data.decode())
    assert row["decision"] == "QUARANTINE"
    assert row["reason_code"] == "SECRET_MATCH"
    assert "_rev" not in row and "delivered" not in row
    assert transport.response.read_sizes == [4097]


@pytest.mark.parametrize("response", [Response(500, b"DEMO_SECRET_ALPHA_2026"), Response(200, b"Code: 395. DEMO_SECRET_ALPHA_2026"), Response(200, b"", {"X-ClickHouse-Exception-Code": "395"}), Response(302, b""), Response(200, b" " * 5000)])
def test_failure_and_embedded_error_bodies_do_not_acknowledge_or_leak(store, metadata, response, caplog, capsys):
    from analytics.events import record_event
    from analytics.worker import flush_pending

    event = record_event(store, **metadata)
    assert flush_pending(store, sink(Opener(response)))["failed"] == 1
    assert store.get("event", event["event_id"])["delivered"] is False
    assert "DEMO_SECRET_ALPHA_2026" not in caplog.text + str(capsys.readouterr())


def test_raw_transport_error_is_replaced_by_bounded_error(store, metadata):
    from analytics.clickhouse import SinkError
    from analytics.events import record_event

    event = record_event(store, **metadata)
    with pytest.raises(SinkError) as raised:
        sink(Opener(error=OSError("private-provider-password"))).insert([event])
    assert "private-provider-password" not in str(raised.value)
    assert raised.value.__suppress_context__ is True


@pytest.mark.parametrize("url", ["http://clickhouse.example.test/", "https://user:secret@clickhouse.example.test/", "https://clickhouse.example.test/?password=secret", "https://clickhouse.example.test/#secret", "https://clickhouse.example.test/private/path", "https:///", "https://clickhouse.example.test:bad/"])
def test_rejects_unsafe_configuration_before_network(url):
    with pytest.raises(ValueError) as raised:
        sink(url=url)
    assert url not in str(raised.value)


@pytest.mark.parametrize("database", ["default; DROP TABLE secrets", "bad.name", "\nsecret", "", "x" * 65])
def test_database_is_an_allowlisted_sql_identifier(database):
    with pytest.raises(ValueError):
        sink(database=database)


def test_real_urllib_opener_rejects_redirect_before_forwarding_credentials(monkeypatch, store, metadata):
    import analytics.clickhouse as clickhouse
    from analytics.events import record_event

    calls = []

    class RedirectingHTTPSHandler(HTTPSHandler):
        def https_open(self, request):
            calls.append(request.full_url)
            headers = Message()
            headers["Location"] = "https://attacker.example.test/collect"
            response = addinfourl(BytesIO(b""), headers, request.full_url, 302)
            response.msg = "Found"
            return response

    monkeypatch.setattr(clickhouse, "HTTPSHandler", RedirectingHTTPSHandler)
    with pytest.raises(clickhouse.SinkError):
        sink().insert([record_event(store, **metadata)])
    assert len(calls) == 1
    assert "attacker.example.test" not in calls[0]
