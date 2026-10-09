# Agent Memory Gateway presentation

A standalone recorded presentation. Open `index.html` directly, or serve it from the repository root:

```sh
python3 -m http.server 4173 --bind 127.0.0.1 --directory presentation
```

Then open <http://127.0.0.1:4173>. Arrow keys navigate; Space plays or pauses; Escape closes an open overlay. The page makes no gateway or sponsor API calls and needs no credentials.

The main replay button brings the comparison into view. Its smaller play/pause control stays beside the chapter controls. The expand button enters a compact full-screen presentation. Motion follows the browser's reduced-motion preference.

The original, editable [Figma concept](https://www.figma.com/design/UpEbOEfZFJ2Cy0G2JJwhcy?node-id=1-2) was created with the Figma MCP. This standalone implementation lives entirely in `presentation/` and does not replace the existing demo UI.

## What is recorded

`evidence.js` contains seven AWS gateway traces checked against ClickHouse on October 9, 2026 at 22:08:34 UTC: four ALLOW, one DENY, and two QUARANTINE decisions. Its source is the sanitized, ignored operator export `build/final-demo-analytics.json`. That export records a read-only verification with zero missing or duplicate traces and passing metadata privacy checks. `recordedAt` is the verification time, not the timestamp of every request.

Trace IDs, operations, decisions, reasons, and timings come from that export. Timings retain their measured values; display rounding is cosmetic. The source link is the official Senso knowledge-base documentation. A report creation was allowed, but the export has no public report URL, so `reportUrl` is `null`.

The baseline is a local mock. The scenario animation, diagrams, redacted sample text, and playback duration illustrate the story. They are not new measurements or a live run. Decision metadata alone does not prove retrieval contents, identical input hashes, caller language, runtime isolation, or a universal detection rate.

## Refreshing the evidence safely

Use a fresh, sanitized export of actual recorded events after a read-only verification. Do not copy raw demo output, request bodies, provider errors, tokens, or private source text into this directory.

1. Confirm the export passed verification, all expected traces are present exactly once, and its metadata privacy checks passed. Validate each row with `analytics.events.export_event` and require a canonical lowercase 32-hex `trace_id`.
2. Rebuild `window.DEMO_EVIDENCE` from an explicit allowlist. The event keys are only `label`, `traceId`, `operation`, `decision`, `reason`, `gateMs`, `sensoMs`, and `totalMs`. Use fixed friendly labels, the gateway's closed operation/decision/reason enums, and finite nonnegative numeric timings. Never copy arbitrary strings or unknown fields from the export.
3. Derive summary counts from those validated rows. Preserve the scope labels `Local mock baseline` and `Recorded AWS run`; update the verification time and limitations honestly. Keep `reportUrl: null` unless the verified metadata supplies the actual public report URL. Never search private storage for a link.
4. Serialize as JSON with non-finite values rejected and `<`, `>`, and `&` escaped. Keep the object, nested objects, events, and arrays frozen. Keep `sourceUrl` restricted to the official public documentation URL. Load the asset as a classic script so it works from `file://` without fetching local JSON.
5. Compare every exported trace and timing with its source; verify the top-level and event key allowlists, uniqueness, summary totals, and deep freezing. Open the presentation to check replay controls and the evidence table. Do not turn a recorded replay into a live integration claim.

The top-level contract is `recordedAt`, `source`, `verified`, `scope`, `events`, `summary`, `reportUrl`, `sourceUrl`, and `limitations`. Only publish this deliberately reduced asset; the ignored operator export stays outside the presentation.
