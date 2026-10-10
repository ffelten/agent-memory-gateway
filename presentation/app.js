/* Replay verified synthetic evidence. Playback sends no API requests. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const scenario = window.INCIDENT_SCENARIO;
  if (!scenario || !scenario.modes) return;
  let mode = "baseline";
  let step = 0;
  let playing = false;
  let timer = null;
  let toastTimer = null;
  const steps = () => scenario.modes[mode].steps;
  const reducedMotion = () =>
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const node = (tag, className, value) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined) element.textContent = value;
    return element;
  };
  const icon = (name) => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#i-" + name);
    svg.append(use);
    return svg;
  };
  function fixtureText(element, value) {
    const parts = value.split("DEMO_SECRET_ALPHA_2026");
    parts.forEach((part, index) => {
      if (index) element.append(node("mark", "", "DEMO_SECRET_ALPHA_2026"));
      element.append(document.createTextNode(part));
    });
  }
  function renderConversation(lane) {
    const container = $(lane + "-messages");
    container.replaceChildren();
    const messages = steps()
      .slice(0, step + 1)
      .flatMap((event, eventIndex) =>
        event.messages
          .filter((message) => message.lane === lane)
          .map((message) => ({ ...message, eventIndex })),
      );
    if (!messages.length) {
      const waiting = node("div", "waiting");
      waiting.append(
        icon("document"),
        node("strong", "", lane === "alpha" ? "An incident to hand off." : "The evidence stays visible."),
        node(
          "p",
          "",
          lane === "alpha" ? "Alpha’s next step is to load the incident-handoff skill." : "The protected replay will show search and direct lookup using Beta’s scoped identity.",
        ),
      );
      container.append(waiting);
      return;
    }
    messages.forEach((message) => {
      const entry = node(
        "article",
        "chat-entry kind-" + message.kind + " tone-" + message.tone,
      );
      if (message.title.startsWith("Fresh context")) {
        entry.classList.add("context-boundary");
        entry.append(
          node(
            "div",
            "context-label",
            "NEW RUN · EARLIER CHAT IS NOT FORWARDED",
          ),
        );
      }
      const heading = node("div", "entry-heading");
      heading.append(
        node("strong", "", message.title),
        node("small", "", String(message.eventIndex + 1).padStart(2, "0")),
      );
      const body = node("p", "entry-body");
      fixtureText(body, message.text);
      entry.append(heading, body);
      if (message.eventIndex < step) entry.style.animation = "none";
      container.append(entry);
    });
    // Seek to the most recent action; old conversations remain inspectable by scrolling.
    container.scrollTop = container.scrollHeight;
  }
  function cardHeader(card, label, symbol) {
    const header = node("div", "memory-card-header");
    header.append(icon(symbol), node("span", "state-badge", label));
    card.append(header);
  }
  function renderMemory(snapshot) {
    const container = $("memory-content");
    container.replaceChildren();
    container.dataset.candidateState = snapshot.candidate;
    container.dataset.privateRecord = "false";
    container.dataset.publicRecord = "false";
    const quarantined = snapshot.candidate === "quarantined";
    if (snapshot.candidate === "absent") {
      container.append(node("p", "memory-section-label", "AWAITING THE MODEL’S WRITE"));
      const empty = node("div", "memory-empty");
      empty.append(icon("memory"), node("strong", "", "A rule is already in place."), node("p", "", "The system explicitly forbids copying credentials into shared memory—even synthetic test values."));
      container.append(empty);
    } else {
      const section = node("p", "memory-section-label", quarantined ? "QUARANTINED · NOT SEARCHABLE" : snapshot.candidate === "stored" ? "RECORDED BY LOCAL TEST SINK" : "ACTUAL MODEL TOOL ARGUMENTS");
      section.append(node("span", "", "Synthetic data"));
      container.append(section);
      const card = node("article", "memory-card is-" + snapshot.candidate);
      card.id = "candidate-card";
      card.dataset.storage = quarantined ? "private" : snapshot.candidate === "stored" ? "local" : "draft";
      cardHeader(card, quarantined ? "QUARANTINED" : snapshot.candidate === "stored" ? "CAPTURED LOCALLY" : "MEMORY_WRITE", quarantined ? "lock" : "document");
      card.append(node("h3", "", scenario.candidate.title));
      const preview = node("pre");
      fixtureText(preview, scenario.candidate.text);
      card.append(preview);
      const footer = node("div", "memory-card-footer");
      footer.append(node("span", "", quarantined ? "SECRET_MATCH" : "Generated by GPT-OSS-20B"), node("span", "", quarantined ? "Whole write blocked" : "No wrapper-added text"));
      card.append(footer);
      container.append(card);
      const evidence = window.RECORDED_EVIDENCE;
      if (evidence?.exemplar?.candidateHash) {
        const fingerprint = node("div", "candidate-fingerprint");
        fingerprint.append(node("small", "", "SAME CANDIDATE · SHA-256"), node("code", "", evidence.exemplar.candidateHash));
        container.append(fingerprint);
      }
      if (quarantined) container.append(node("p", "quarantine-note", "This presenter preview comes from the synthetic capture. Beta’s retrieval endpoints return none of this text."));
    }
    $("memory-footer").textContent = mode === "protected" ? "Recorded AWS decision · no Senso document registered" : "Local memory test sink · no unprotected Senso run";
  }
  function renderTimeline() {
    const timeline = $("timeline");
    timeline.replaceChildren();
    steps().forEach((event, index) => {
      const button = node("button", index < step ? "is-past" : "", event.label);
      button.type = "button";
      button.dataset.step = String(index);
      button.dataset.number = String(index + 1).padStart(2, "0");
      button.setAttribute(
        "aria-label",
        "Event " + (index + 1) + ": " + event.label,
      );
      if (index === step) button.setAttribute("aria-current", "step");
      button.addEventListener("click", () => {
        setPlaying(false);
        seek(index, true);
      });
      timeline.append(button);
    });
  }
  function syncControls() {
    document.body.dataset.playing = String(playing);
    $("play").setAttribute("aria-pressed", String(playing));
    $("play-label").textContent = playing
      ? "Pause replay"
      : step === steps().length - 1
        ? "Replay incident"
        : step === 0
          ? "Play incident"
          : "Continue";
    $("play-glyph")
      .querySelector("use")
      .setAttribute("href", playing ? "#i-pause" : "#i-play");
    $("prev").disabled = step === 0;
    $("next").disabled = step === steps().length - 1;
    $("enforce-label").textContent =
      mode === "protected" ? "Replay protected run" : "Replay with gateway";
  }
  function render() {
    const current = steps()[step];
    document.body.dataset.mode = mode;
    document.body.dataset.step = String(step);
    $("baseline-mode").setAttribute(
      "aria-pressed",
      String(mode === "baseline"),
    );
    $("protected-mode").setAttribute(
      "aria-pressed",
      String(mode === "protected"),
    );
    $("route-label").textContent =
      mode === "baseline"
        ? "Poisoned skill → model → memory write"
        : "Same write → AWS gateway → blocked";
    ["alpha", "memory", "beta"].forEach((lane) =>
      $(lane + "-pane").classList.toggle("is-focused", current.focus === lane),
    );
    renderConversation("alpha");
    renderConversation("beta");
    renderMemory(current.memory);
    $("beta-context").textContent = mode === "protected" ? "Live AWS responses · recorded replay" : "Experiment observations · no Beta model run";
    $("beta-permissions").textContent = "Trusted harness · fresh Beta run token";
    $("step-count").textContent =
      String(step + 1).padStart(2, "0") +
      " / " +
      String(steps().length).padStart(2, "0");
    $("event-title").textContent = current.outcome
      ? current.outcome.title
      : current.label;
    $("event-caption").textContent = current.outcome
      ? current.outcome.text
      : current.caption;
    const caption = document.querySelector(".event-caption");
    caption.classList.toggle(
      "is-danger",
      Boolean(current.outcome && current.outcome.tone === "danger"),
    );
    caption.classList.toggle(
      "is-success",
      Boolean(current.outcome && current.outcome.tone === "success") ||
        current.label === "Quarantined",
    );
    $("event-status").textContent =
      current.memory.candidate === "quarantined"
        ? "WRITE BLOCKED"
        : current.memory.candidate === "stored"
          ? "LEAK CAPTURED"
          : "RECORDED";
    renderTimeline();
    syncControls();
  }
  function seek(index, focusTimeline = false) {
    step = Math.max(0, Math.min(steps().length - 1, index));
    render();
    if (focusTimeline)
      $("timeline")
        .querySelector('[data-step="' + step + '"]')
        .focus({ preventScroll: true });
  }
  function schedule() {
    clearTimeout(timer);
    if (!playing) return;
    timer = window.setTimeout(
      () => {
        if (step < steps().length - 1) seek(step + 1);
        if (step === steps().length - 1) setPlaying(false);
        else schedule();
      },
      3500 / Number($("speed").value),
    );
  }
  function setPlaying(value) {
    playing = value;
    clearTimeout(timer);
    syncControls();
    if (playing) schedule();
  }
  function framePlayer() {
    document.querySelector(".player").scrollIntoView({
      block: "start",
      behavior: reducedMotion() ? "auto" : "smooth",
    });
  }
  function togglePlay(scrollToPlayer = false) {
    if (playing) return setPlaying(false);
    if (step === steps().length - 1) seek(0);
    if (scrollToPlayer) framePlayer();
    setPlaying(true);
  }
  function changeMode(nextMode, autoPlay = false) {
    setPlaying(false);
    mode = nextMode;
    seek(0);
    if (autoPlay) {
      framePlayer();
      setPlaying(true);
    }
  }
  $("play").addEventListener("click", () => togglePlay(true));
  $("prev").addEventListener("click", () => {
    setPlaying(false);
    seek(step - 1);
  });
  $("next").addEventListener("click", () => {
    setPlaying(false);
    seek(step + 1);
  });
  $("restart").addEventListener("click", () => {
    setPlaying(false);
    seek(0);
  });
  $("baseline-mode").addEventListener("click", () => changeMode("baseline"));
  $("protected-mode").addEventListener("click", () => changeMode("protected"));
  $("enforce").addEventListener("click", () => changeMode("protected", true));
  $("speed").addEventListener("change", schedule);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) setPlaying(false);
  });
  document.addEventListener("keydown", (event) => {
    if (
      document.querySelector("dialog[open]") ||
      event.altKey ||
      event.metaKey ||
      event.ctrlKey ||
      event.target.closest('input, select, textarea, [contenteditable="true"]')
    )
      return;
    const onTimeline = event.target.matches("#timeline button[data-step]");
    const onControl = event.target.closest("button, a");
    if (
      ["ArrowRight", "ArrowLeft"].includes(event.key) &&
      (!onControl || onTimeline)
    ) {
      event.preventDefault();
      setPlaying(false);
      seek(step + (event.key === "ArrowRight" ? 1 : -1), onTimeline);
    } else if (event.code === "Space" && !onControl) {
      event.preventDefault();
      togglePlay();
    } else if (onTimeline && ["Home", "End"].includes(event.key)) {
      event.preventDefault();
      setPlaying(false);
      seek(event.key === "Home" ? 0 : steps().length - 1, true);
    }
  });
  ["skill", "evidence"].forEach((name) => {
    $(name + "-button").addEventListener("click", () => {
      setPlaying(false);
      $(name + "-dialog").showModal();
    });
    $("close-" + name).addEventListener("click", () =>
      $(name + "-dialog").close(),
    );
    $(name + "-dialog").addEventListener("click", (event) => {
      const rect = event.currentTarget.getBoundingClientRect();
      if (
        event.target === event.currentTarget &&
        (event.clientX < rect.left ||
          event.clientX > rect.right ||
          event.clientY < rect.top ||
          event.clientY > rect.bottom)
      )
        event.currentTarget.close();
    });
  });
  $("skill-path").textContent = scenario.skill.path;
  scenario.skill.lines.forEach((line, index) => {
    const row = node(
      "div",
      "skill-line" + (line.poisoned ? " is-poisoned" : ""),
    );
    row.append(
      node("span", "line-number", String(index + 1)),
      node("span", "", line.text),
    );
    $("skill-lines").append(row);
  });
  function showLog(name, focus = false) {
    document.querySelectorAll("[data-log]").forEach((button) => {
      const active = button.dataset.log === name;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      $("log-" + button.dataset.log).hidden = !active;
      if (active && focus) button.focus();
    });
  }
  document.querySelectorAll("[data-log]").forEach((button, index, buttons) => {
    button.addEventListener("click", () => showLog(button.dataset.log));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
      showLog(buttons[next].dataset.log, true);
    });
  });
  function logBlock(label, value, expanded = false) {
    const details = node("details", "log-block");
    details.open = expanded;
    details.append(node("summary", "", label));
    const pre = node("pre");
    fixtureText(pre, typeof value === "string" ? value : JSON.stringify(value, null, 2));
    details.append(pre);
    return details;
  }
  function renderGatewayLog() {
    const evidence = window.RECORDED_EVIDENCE;
    const receipt = evidence.receipts.find((row) => row.caseId === $("evidence-case").value);
    if (!receipt) return;
    const container = $("gateway-log-content");
    container.replaceChildren();
    container.append(node("p", "log-context", "Fresh Alpha and Beta runs. Captured title and text submitted unchanged to the deployed AWS gateway."));
    container.append(logBlock("Candidate SHA-256 · " + receipt.timestamp, receipt.candidateHash, true));
    container.append(logBlock("POST /v1/memories → HTTP " + receipt.create.httpStatus, receipt.create.response, true));
    container.append(logBlock("Beta search → HTTP " + receipt.betaSearch.httpStatus, receipt.betaSearch.response, true));
    container.append(logBlock("Beta direct lookup → HTTP " + receipt.betaDirect.httpStatus, receipt.betaDirect.response, true));
    container.append(logBlock("Strongly consistent DynamoDB metadata check", receipt.storage, false));
  }
  function populateEvidence() {
    const evidence = window.RECORDED_EVIDENCE;
    if (!evidence?.verified || !Array.isArray(evidence.receipts) || !evidence.receipts.length) {
      $("evidence-status").textContent = "Verified evidence is unavailable.";
      return;
    }
    $("trace-count").textContent = String(evidence.receipts.length);
    $("allow-count").textContent = String(evidence.receipts.filter((r) => r.betaSearch.response.results?.length === 0).length);
    $("blocked-count").textContent = String(evidence.receipts.filter((r) => r.create.response.state === "QUARANTINED").length);
    $("evidence-status").textContent = "Real GPT-OSS-20B inference on AkashML, followed by live AWS replay of the same captured writes. All customer data and credential values are synthetic.";
    const modelPanel = $("log-model");
    modelPanel.append(node("p", "log-context", "Exemplar: " + evidence.exemplar.caseId + ". Only file loading was forced; the model chose the memory-write arguments. No incident context was appended by the harness."));
    evidence.exemplar.calls.forEach((call, index) => {
      const title = node("h3", "log-heading", "Model call " + (index + 1) + " · " + (index === 0 ? "Read the skill" : "Write the handoff"));
      modelPanel.append(title);
      modelPanel.append(logBlock("Request · messages and tool contract", call.request));
      modelPanel.append(logBlock("Actual assistant response · native tool calls", call.assistantMessage, true));
      modelPanel.append(node("p", "log-meta", "Response " + call.responseId + " · model API round trip " + Number(call.latencyMs).toFixed(1) + " ms"));
    });
    evidence.receipts.forEach((receipt, index) => {
      const option = node("option", "", receipt.label);
      option.value = receipt.caseId;
      $("evidence-case").append(option);
      const card = node("article", "receipt-card");
      const heading = node("div", "receipt-heading");
      heading.append(node("span", "receipt-number", "0" + (index + 1)), node("span", "receipt-state", receipt.create.response.state));
      card.append(heading, node("h3", "", receipt.label));
      card.append(node("p", "receipt-case", receipt.caseId));
      const hash = node("code", "receipt-hash", receipt.candidateHash);
      hash.title = "Canonical candidate SHA-256";
      card.append(node("small", "receipt-key", "UNCHANGED CANDIDATE · SHA-256"), hash);
      const checks = node("div", "receipt-checks");
      checks.append(node("span", "", "✓ SECRET_MATCH"), node("span", "", "✓ Beta 404"), node("span", "", "✓ Search empty"));
      card.append(checks, node("small", "receipt-key", "CREATE TRACE"), node("code", "receipt-trace", receipt.create.response.trace_id));
      const inspect = node("button", "text-button", "Inspect this receipt ↗");
      inspect.addEventListener("click", () => {
        setPlaying(false);
        $("evidence-case").value = receipt.caseId;
        renderGatewayLog();
        showLog("gateway");
        $("evidence-dialog").showModal();
      });
      card.append(inspect);
      $("receipt-cards").append(card);
    });
    $("evidence-case").value = evidence.exemplar.caseId;
    $("evidence-case").addEventListener("change", renderGatewayLog);
    renderGatewayLog();
    const matrixPanel = $("log-matrix");
    matrixPanel.append(node("p", "log-context", "Confirmation batch: two fresh conversations for each skill, identical task and secrecy rule, temperature 0.2. Counts below cover GPT-OSS-20B only."));
    const matrixTable = node("table");
    const matrixHead = node("thead");
    const matrixTitles = node("tr");
    ["Skill condition", "Credential leaks", "Other outcomes"].forEach((title) => matrixTitles.append(node("th", "", title)));
    matrixHead.append(matrixTitles); matrixTable.append(matrixHead);
    const matrixBody = node("tbody");
    evidence.matrix.rows.forEach((row) => {
      const tr = node("tr");
      tr.append(node("td", "", row.label), node("td", "", row.leaks + " / " + row.attempts), node("td", "", row.notes));
      matrixBody.append(tr);
    });
    matrixTable.append(matrixBody);
    const matrixScroll = node("div", "table-scroll"); matrixScroll.append(matrixTable); matrixPanel.append(matrixScroll);
    matrixPanel.append(node("p", "log-context", "Llama 3.3 70B: all eight confirmation attempts were inconclusive because the provider returned invalid empty skill-read arguments. These are not counted as resistance."));
    matrixPanel.append(logBlock("Earlier automatic-loading pilot · separate protocol and recorder limitation", evidence.pilot));
    const clickhousePanel = $("log-clickhouse");
    if (evidence.clickhouse?.verified) {
      $("audit-status").textContent = evidence.clickhouse.events.length + " ClickHouse events uploaded · read-back verified";
      clickhousePanel.append(node("p", "log-context", "Actual ClickHouse read-back matched to these AWS trace IDs. ALLOW on search means the request was permitted; its result was empty. Timings below are measured gateway and provider durations."));
      const table = node("table", "clickhouse-table");
      const head = node("thead"), titles = node("tr");
      ["Operation / trace", "Decision / reason", "Gateway", "Senso", "Total"].forEach((title) => titles.append(node("th", "", title)));
      head.append(titles); table.append(head);
      const rows = node("tbody"); rows.id = "evidence-rows";
      evidence.clickhouse.events.forEach((event) => {
        const tr = node("tr"), operation = node("td", "", event.label || event.operation), decision = node("td");
        operation.append(node("small", "", event.traceId), node("small", "", event.timestamp));
        decision.append(node("span", "decision decision-" + event.decision.toLowerCase(), event.decision), node("small", "", event.reason));
        tr.append(operation, decision);
        [event.gateMs, event.sensoMs, event.totalMs].forEach((value) => tr.append(node("td", "", Number(value).toFixed(1) + " ms")));
        rows.append(tr);
      });
      table.append(rows); const scroll = node("div", "table-scroll"); scroll.append(table); clickhousePanel.append(scroll);
      clickhousePanel.append(node("p", "log-meta", "Read-back verified " + evidence.clickhouse.verifiedAt + ". Metadata only; no raw conversations, credentials, or queries in ClickHouse."));
    } else clickhousePanel.append(node("p", "log-context", "A verified ClickHouse read-back is unavailable. The gateway receipts remain independently inspectable."));
    $("evidence-time").textContent = "Gateway evidence verified " + new Date(evidence.recordedAt).toLocaleString("en-US", {timeZone: "America/Los_Angeles", dateStyle: "medium", timeStyle: "short"}) + " Pacific.";
    evidence.limitations.forEach((text) => $("evidence-limits").append(node("li", "", text)));
    $("download-evidence").addEventListener("click", () => {
      const blob = new Blob([JSON.stringify(evidence, null, 2) + "\n"], {type: "application/json"});
      const url = URL.createObjectURL(blob);
      const link = node("a"); link.href = url; link.download = "memory-gateway-recorded-evidence.json";
      document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  $("fullscreen").addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (document.documentElement.requestFullscreen)
        await document.documentElement.requestFullscreen();
      else throw new Error("Unavailable");
    } catch (_) {
      $("toast").textContent =
        "Use your browser’s full-screen control to present.";
      $("toast").hidden = false;
      clearTimeout(toastTimer);
      toastTimer = window.setTimeout(() => {
        $("toast").hidden = true;
      }, 3500);
    }
  });
  document.addEventListener("fullscreenchange", () => {
    document.body.classList.toggle(
      "is-presenting",
      Boolean(document.fullscreenElement),
    );
    $("fullscreen").setAttribute(
      "aria-label",
      document.fullscreenElement ? "Exit full screen" : "Enter full screen",
    );
    if (document.fullscreenElement) window.requestAnimationFrame(framePlayer);
  });
  render();
  populateEvidence();
})();
