/* Recorded metadata only. See README.md for provenance and refresh rules. */
(() => {
  "use strict";
  const evidence = {
    recordedAt: "2026-10-09T22:08:34.592152+00:00",
    source:
      "Recorded AWS gateway events, verified by a read-only ClickHouse query",
    verified: true,
    scope: {
      baseline: "Local mock baseline",
      protected: "Recorded AWS run",
    },
    events: [
      {
        label: "Candidate admission",
        traceId: "9da640d2eaf9401da6ad923b8e16377e",
        operation: "create_memory",
        decision: "QUARANTINE",
        reason: "SECRET_MATCH",
        gateMs: 144.40961299999344,
        sensoMs: 0.0,
        totalMs: 144.40961299999344,
      },
      {
        label: "Memory search",
        traceId: "e050dd1aae454e27b824c422d375a9f9",
        operation: "search",
        decision: "ALLOW",
        reason: "OK",
        gateMs: 131.5064689999872,
        sensoMs: 0.0,
        totalMs: 131.5064689999872,
      },
      {
        label: "Direct memory lookup",
        traceId: "08d511613c034f0c91def382bbb3b11c",
        operation: "get_memory",
        decision: "DENY",
        reason: "NOT_READABLE",
        gateMs: 42.08011599996553,
        sensoMs: 0.0,
        totalMs: 42.08011599996553,
      },
      {
        label: "Second candidate admission",
        traceId: "c0f82d4f8ca3487b9a168ce89078982c",
        operation: "create_memory",
        decision: "QUARANTINE",
        reason: "SECRET_MATCH",
        gateMs: 126.87426499996945,
        sensoMs: 0.0,
        totalMs: 126.87426499996945,
      },
      {
        label: "Permitted memory admission",
        traceId: "466b1370825d4684b8a4e3e96056cf39",
        operation: "create_memory",
        decision: "ALLOW",
        reason: "OK",
        gateMs: 191.5150759999733,
        sensoMs: 546.6360760000271,
        totalMs: 738.1511520000004,
      },
      {
        label: "Memory revocation",
        traceId: "44e5e19192ac48aaba04a9b754200117",
        operation: "revoke",
        decision: "ALLOW",
        reason: "REVOKED",
        gateMs: 10.569382000028327,
        sensoMs: 0.0,
        totalMs: 10.569382000028327,
      },
      {
        label: "Public report creation",
        traceId: "300a3abf08bc47d8897c416d22a77a30",
        operation: "create_report",
        decision: "ALLOW",
        reason: "OK",
        gateMs: 126.71181199999637,
        sensoMs: 0.0,
        totalMs: 126.71181199999637,
      },
    ],
    summary: {
      traces: 7,
      allow: 4,
      deny: 1,
      quarantine: 2,
    },
    reportUrl: null,
    sourceUrl: "https://docs.senso.ai/docs/knowledge-base",
    limitations: [
      "The baseline is a local mock, not a verified live unprotected Senso run.",
      "This replay is recorded and makes no gateway or sponsor API calls.",
      "Audit metadata records decisions and timings; it does not include retrieval bodies or prove a universal detection rate.",
      "Report creation was allowed, but the exported evidence does not include its public URL.",
      "Timings describe these seven requests and are not a latency guarantee.",
    ],
  };
  Object.freeze(evidence.scope);
  evidence.events.forEach(Object.freeze);
  Object.freeze(evidence.events);
  Object.freeze(evidence.summary);
  Object.freeze(evidence.limitations);
  window.DEMO_EVIDENCE = Object.freeze(evidence);
})();
