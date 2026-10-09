# Agent Memory Gateway presentation

Public Cloudflare URL: [https://memory-gateway-demo.ashishranjan2404.workers.dev](https://memory-gateway-demo.ashishranjan2404.workers.dev). Deployment instructions: [Cloudflare static hosting](../infra/cloudflare-presentation/README.md).

A standalone incident player with three persistent panes: Alpha, shared memory, and Beta. Open `index.html` directly, or serve it from the repository root:

```sh
python3 -m http.server 4173 --bind 127.0.0.1 --directory presentation
```

Then open <http://127.0.0.1:4173>. Arrow keys navigate; Space plays or pauses; Escape closes an open overlay. The page makes no gateway or sponsor API calls and needs no credentials.

Play, pause, or seek through the incident at 1× or 2× speed. Play brings the player into view; the full-screen control provides a compact presentation layout. Starting the protected replay resets the player to a fresh history and memory state. Motion follows the browser's reduced-motion preference.

This standalone implementation lives entirely in `presentation/` and does not replace the existing demo UI.

The editable [warm Figma concept](https://www.figma.com/design/UpEbOEfZFJ2Cy0G2JJwhcy?node-id=5-2) was created through the Figma MCP. It preserves the earlier concept as a separate frame.

## What is illustrated

`scenario.js` authors both incident paths. Every conversation and tool response is scripted. The label **Haiku · simulated** names the illustrative agent; no Haiku model or other provider is called. The displayed credential `DEMO_ALPHA_KEY` and customer record are invented presentation fixtures, not the deployed gateway's canary or actual customer data.

The poisoned `SKILL.md` exists only as JavaScript display text. It is never written to a skills directory, installed, or executed. In the story, Alpha reads the skill, follows its instruction to preserve original incident context including a credential, and submits the handoff to shared memory. The unprotected illustration shows Beta retrieving and reusing that credential after its own key failed.

The protected illustration starts fresh and uses the same candidate text. The whole candidate is quarantined before ingestion; both search and direct lookup keep it unreadable. A separate, previously approved Alpha record remains available to Alpha and is denied to Beta. A trusted harness then starts a separate Beta publishing context containing only an independently approved public source. The public note is an illustrated result of that source, never a sanitized version of the quarantined candidate. No live report or webpage is created by playback.

Messages accumulate visually through the selected step; the explicit “Fresh context” message marks a new publishing conversation, not reuse of earlier private context. Every step declares a complete memory snapshot. Recorded receipts below are separate evidence and do not authenticate these invented conversations or tool outputs.

## What is recorded

`evidence.js` contains seven AWS gateway traces checked against ClickHouse on October 9, 2026 at 22:08:34 UTC: four ALLOW, one DENY, and two QUARANTINE decisions. Its source is the sanitized, ignored operator export `build/final-demo-analytics.json`. That export records a read-only verification with zero missing or duplicate traces and passing metadata privacy checks. `recordedAt` is the verification time, not the timestamp of every request.

Trace IDs, operations, decisions, reasons, and timings come from that export. Timings retain their measured values; display rounding is cosmetic. The source link is the official Senso knowledge-base documentation. A report creation was allowed, but the export has no public report URL, so `reportUrl` is `null`.

The earlier recorded demo's baseline was a local mock. The incident player is a new authored illustration, not a replay of those exact conversations. Playback duration is not a measurement. Decision metadata alone does not prove retrieval contents, identical input hashes, caller language, runtime isolation, or a universal detection rate.

## Refreshing the evidence safely

Use a fresh, sanitized export of actual recorded events after a read-only verification. Do not copy raw demo output, request bodies, provider errors, tokens, or private source text into this directory.

1. Confirm the export passed verification, all expected traces are present exactly once, and its metadata privacy checks passed. Validate each row with `analytics.events.export_event` and require a canonical lowercase 32-hex `trace_id`.
2. Rebuild `window.DEMO_EVIDENCE` from an explicit allowlist. The event keys are only `label`, `traceId`, `operation`, `decision`, `reason`, `gateMs`, `sensoMs`, and `totalMs`. Use fixed friendly labels, the gateway's closed operation/decision/reason enums, and finite nonnegative numeric timings. Never copy arbitrary strings or unknown fields from the export.
3. Derive summary counts from those validated rows. Preserve the scope labels `Local mock baseline` and `Recorded AWS run`; update the verification time and limitations honestly. Keep `reportUrl: null` unless the verified metadata supplies the actual public report URL. Never search private storage for a link.
4. Serialize as JSON with non-finite values rejected and `<`, `>`, and `&` escaped. Keep the object, nested objects, events, and arrays frozen. Keep `sourceUrl` restricted to the official public documentation URL. Load the asset as a classic script so it works from `file://` without fetching local JSON.
5. Compare every exported trace and timing with its source; verify the top-level and event key allowlists, uniqueness, summary totals, and deep freezing. Open the presentation to check replay controls and the evidence table. Do not turn a recorded replay into a live integration claim.

The top-level contract is `recordedAt`, `source`, `verified`, `scope`, `events`, `summary`, `reportUrl`, `sourceUrl`, and `limitations`. Only publish this deliberately reduced asset; the ignored operator export stays outside the presentation.
