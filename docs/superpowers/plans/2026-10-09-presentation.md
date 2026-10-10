# Standalone Memory Gateway Presentation

**Goal:** Build the user's requested fresh, simple, visually powerful demo without inspecting or changing Florian's UI.

**Architecture:** A standalone buildless presentation in `presentation/` uses local HTML, CSS, JavaScript, and a sanitized evidence export. Four scenes reveal access denied, the leak, quarantine, and useful work. A separate Figma concept establishes the visual direction. All interaction replays captured evidence; no browser credentials or live mutations.

**Design:** Charcoal canvas, oversized editorial title, coral baseline, mint protected result, violet shared-memory diagram. Keep both outcomes visible. One primary replay button, four chapter buttons, compact evidence drawer, keyboard navigation, reduced-motion behavior, responsive layouts.

**Constraints:** Work on `codex/presentation`. Only new presentation assets and related documentation. Preserve the existing demo, gateway, adapter, and deployment. Label the baseline as a local mock and protection as a recorded AWS run. Actual ClickHouse metadata may be used only after allowlisting. Clearly mark illustrative story content and incomplete evidence. No keys, run tokens, raw queries, private candidate text, or source addresses enter artifacts.

- [x] Create a fresh Figma concept with available design MCP tools; retain a link or explain connection limitation. https://www.figma.com/design/UpEbOEfZFJ2Cy0G2JJwhcy?node-id=1-2
- [x] Build the four-scene standalone UI and sanitized evidence asset.
- [x] Verify replay, chapter navigation, evidence drawer, keyboard access, mobile layout, and reduced motion in a browser; inspect screenshots. 78 browser checks passed on 1440×1000 and 390×844, including automatic stage scrolling and direct file loading.
- [x] Run focused privacy and gateway regression checks; review the finished presentation independently. 192 gateway checks passed, one backend-specific skip; independent static review found no important issues.
- [x] Commit, publish the branch/PR, and provide a local working preview and handoff. PR #18: https://github.com/ffelten/agent-memory-gateway/pull/18. Open `presentation/index.html` directly; the five-file ZIP is in ignored `build/memory-gateway-presentation.zip`.
