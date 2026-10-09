-- Apply in the database configured in CLICKHOUSE_SECRET_ARN.
-- Rows are metadata only. Retry delivery may repeat event_id.
CREATE TABLE IF NOT EXISTS gateway_events
(
    event_id String,
    trace_id String,
    tenant_id Nullable(String),
    principal_id Nullable(String),
    run_id Nullable(String),
    memory_id Nullable(String),
    application_version Nullable(UInt32),
    operation LowCardinality(String),
    transport LowCardinality(String),
    decision LowCardinality(String),
    reason_code LowCardinality(String),
    policy_version LowCardinality(String),
    gate_ms Float64,
    senso_ms Float64,
    total_ms Float64,
    timestamp DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree
ORDER BY (timestamp, event_id);

-- Use FINAL for accurate counts before background replacement merges settle.
-- SELECT timestamp AS ts, trace_id, decision, gate_ms, senso_ms, total_ms
-- FROM gateway_events FINAL ORDER BY timestamp DESC LIMIT 100;
