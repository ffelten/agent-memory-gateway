# Poisoned Skill Incident Player Implementation Plan

**Goal:** Build the user-approved option C: a clearly illustrated poisoned-skill incident with persistent Alpha, memory, and Beta panes, followed by a fresh protected replay.

**Architecture:** Buildless HTML/CSS/JS in presentation/. scenario.js provides authored synthetic steps; evidence.js retains separately verified protected AWS metadata. A deterministic step reducer reconstructs each frame so seeking, restart, and mode changes cannot preserve a leaked context.

**Tech Stack:** Browser DOM, native dialog, CSS, local Python HTTP server, Playwright with existing Chrome.

## Global constraints

- Do not inspect or modify Florian's demo UI, agents, adapter, or gateway behavior.
- Persistent “Illustrated scenario” and “Haiku · simulated” labels; no model/provider execution or new measurement claims.
- The poisoned SKILL.md is display-only text in scenario.js, never installed or executed.
- Whole candidate is quarantined; independent public source and private memory remain separate objects.
- Protected replay resets all chat and memory state; no production policy switch.
- Recorded ClickHouse decisions are separate metadata receipts, not proof of invented chats.
- Warm ivory, terracotta, charcoal, sage; responsive and keyboard accessible; no external assets or credentials.

## Tasks

- [x] Author scenario.js and update README: window.INCIDENT_SCENARIO with skill {path,lines:[{text,poisoned}]}, candidate {title,text}, modes baseline/protected {title,steps:[{label,caption,focus,messages:[{lane,kind,title,text,tone}],memory,outcome?}]}. Messages accumulate through selected step. memory is full snapshot {candidate:absent|draft|stored|quarantined,privateRecord:boolean,publicRecord:boolean}. outcome {tone,title,text}. focus alpha|memory|beta. All text authored synthetic illustration.
- [x] Replace index.html/styles.css/app.js with three persistent panes, step seek/play/pause, fresh replay mode reset, skill inspection and evidence dialogs. Timed playback stops at end; does not automatically enable protection. Use textContent for all scenario and evidence strings.
- [x] Verify full baseline/protected traversal, mode reset, keyboard/dialog behavior, screenshots at desktop/mobile, file URL support, reduced motion, no overflow/errors/external API calls, metadata asset unchanged.
- [ ] Review screenshots and truthfulness, resolve findings, update PR18, produce portable ZIP and open preview.

## Acceptance

The presenter can show Beta's denial, skill ingestion, unsafe handoff, memory update, Beta retrieval, downstream consequence, then restart with protection and show the stop at admission. Each event has a visible tool or chat message and the current memory snapshot. A separate inspector discloses that conversations are scripted and the real metadata covers the earlier protected AWS run only. Browser tests must confirm state does not leak from baseline into protected mode.

## Verification result

876 browser assertions passed across 1440×1000, 1280×800, and 390×844 viewports, covering both scenario paths, resets, keyboard/dialog behavior, timed playback, file URLs, reduced motion, and local-only asset loading. Targeted fullscreen checks passed after the layout fix. The evidence asset SHA-256 remains identical to the previous committed version. Independent review confirmed the public-only context label and the same-record Alpha/Beta access comparison.
