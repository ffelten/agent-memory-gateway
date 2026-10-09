"""Outbox delivery must survive sink failure and acknowledgement races."""

class RecordingSink:
    def __init__(self, fail=False):
        self.fail = fail
        self.rows = []

    def insert(self, rows):
        if self.fail:
            raise RuntimeError("DEMO_SECRET_ALPHA_2026 provider raw response")
        self.rows.extend(rows)


def test_failed_export_stays_pending_and_retry_acknowledges_only_after_insert(store, metadata, caplog, capsys):
    from analytics.events import record_event
    from analytics.worker import flush_pending

    event = record_event(store, **metadata)
    sink = RecordingSink(fail=True)
    assert flush_pending(store, sink) == {"attempted": 1, "delivered": 0, "failed": 1}
    assert store.get("event", event["event_id"])["delivered"] is False
    sink.fail = False
    assert flush_pending(store, sink) == {"attempted": 1, "delivered": 1, "failed": 0}
    assert store.get("event", event["event_id"])["delivered"] is True
    assert flush_pending(store, sink) == {"attempted": 0, "delivered": 0, "failed": 0}
    assert [row["event_id"] for row in sink.rows] == [event["event_id"]]
    assert "_rev" not in sink.rows[0] and "delivered" not in sink.rows[0]
    assert "DEMO_SECRET_ALPHA_2026" not in caplog.text + str(capsys.readouterr())


def test_acknowledgement_failure_retries_same_event_id_without_loss(store, metadata):
    from analytics.events import record_event
    from analytics.worker import flush_pending

    event = record_event(store, **metadata)
    sink = RecordingSink()
    store.fail_ack = True
    assert flush_pending(store, sink)["delivered"] == 0
    assert store.get("event", event["event_id"])["delivered"] is False
    store.fail_ack = False
    assert flush_pending(store, sink)["delivered"] == 1
    assert [row["event_id"] for row in sink.rows] == [event["event_id"], event["event_id"]]


def test_limit_keeps_unsent_events_pending(store, metadata):
    from analytics.events import record_event
    from analytics.worker import flush_pending

    for _ in range(3):
        record_event(store, **metadata)
    sink = RecordingSink()
    assert flush_pending(store, sink, limit=2)["delivered"] == 2
    assert len([event for event in store.list("event") if not event["delivered"]]) == 1


def test_corrupt_outbox_payload_is_retained_without_export(store, metadata):
    from analytics.events import record_event
    from analytics.worker import flush_pending

    event = record_event(store, **metadata)
    event["query"] = "DEMO_SECRET_ALPHA_2026"
    store.put("event", event["event_id"], event)
    sink = RecordingSink()
    assert flush_pending(store, sink)["failed"] == 1
    assert sink.rows == []
    assert store.get("event", event["event_id"])["delivered"] is False
