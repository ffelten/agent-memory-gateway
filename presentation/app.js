/* All conversations are authored illustrations. Playback sends no requests. */
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
    const parts = value.split("DEMO_ALPHA_KEY");
    parts.forEach((part, index) => {
      if (index) element.append(node("mark", "", "DEMO_ALPHA_KEY"));
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
        node("strong", "", "An incident to hand off."),
        node(
          "p",
          "",
          "Alpha’s next step is to load the incident-handoff skill.",
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
    container.dataset.privateRecord = String(snapshot.privateRecord);
    container.dataset.publicRecord = String(snapshot.publicRecord);
    if (snapshot.candidate !== "absent") {
      const isPrivate = snapshot.candidate === "quarantined";
      const label =
        snapshot.candidate === "draft"
          ? "OUTGOING HANDOFF"
          : isPrivate
            ? "PRIVATE QUARANTINE"
            : "SHARED · RETRIEVABLE";
      const section = node("p", "memory-section-label", label);
      section.append(
        node(
          "span",
          "",
          isPrivate
            ? "Presenter view"
            : snapshot.candidate === "draft"
              ? "Not stored yet"
              : "Readable by Beta",
        ),
      );
      container.append(section);
      const card = node("article", "memory-card is-" + snapshot.candidate);
      card.id = "candidate-card";
      card.dataset.storage = isPrivate
        ? "private"
        : snapshot.candidate === "stored"
          ? "shared"
          : "draft";
      cardHeader(
        card,
        snapshot.candidate.toUpperCase(),
        isPrivate ? "lock" : "document",
      );
      card.append(node("h3", "", scenario.candidate.title));
      const preview = node("pre");
      fixtureText(preview, scenario.candidate.text);
      card.append(preview);
      const footer = node("div", "memory-card-footer");
      footer.append(
        node(
          "span",
          "",
          isPrivate ? "SECRET_MATCH" : "Original incident context",
        ),
        node("span", "", isPrivate ? "Not searchable" : "Synthetic fixture"),
      );
      card.append(footer);
      container.append(card);
      if (isPrivate)
        container.append(
          node(
            "p",
            "quarantine-note",
            "Held before ingestion. This preview belongs to the presenter; neither agent can retrieve this candidate.",
          ),
        );
    } else {
      container.append(
        node("p", "memory-section-label", "SHARED · RETRIEVABLE"),
      );
      container.append(
        node(
          "div",
          "memory-empty",
          "No incident handoff stored.\nWatch this space when Alpha writes.",
        ),
      );
    }
    if (snapshot.privateRecord || snapshot.publicRecord) {
      container.append(
        node("p", "memory-section-label", "INDEPENDENTLY APPROVED"),
      );
    }
    if (snapshot.privateRecord) {
      const card = node("article", "memory-card compact-card");
      card.id = "private-record";
      cardHeader(card, "ALPHA ONLY", "lock");
      card.append(
        node("h3", "", "Alpha customer record"),
        node(
          "p",
          "",
          "A separate approved record. Source permissions stay attached.",
        ),
      );
      container.append(card);
    }
    if (snapshot.publicRecord) {
      const card = node("article", "memory-card compact-card is-public");
      card.id = "public-record";
      cardHeader(card, "PUBLIC", "document");
      card.append(
        node("h3", "", "Knowledge-base runbook"),
        node(
          "p",
          "",
          "An independent public source for the useful troubleshooting note.",
        ),
      );
      const link = node("a", "source-link", "Senso documentation ↗");
      link.href = "https://docs.senso.ai/docs/knowledge-base";
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      card.append(link);
      container.append(card);
    }
    if (
      snapshot.candidate === "absent" &&
      !snapshot.privateRecord &&
      !snapshot.publicRecord
    ) {
      const hint = node("div", "memory-hint");
      hint.append(
        icon("memory"),
        node(
          "p",
          "",
          "A shared store does not inherit the intent of a conversation. It needs an explicit permission boundary.",
        ),
      );
      container.append(hint);
    }
    $("memory-footer").textContent =
      mode === "protected"
        ? "Gateway → Senso · illustrated protected path"
        : "Direct to memory · illustrated unprotected path";
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
        ? "Alpha → shared memory → Beta"
        : "Alpha → gateway → permitted memory";
    ["alpha", "memory", "beta"].forEach((lane) =>
      $(lane + "-pane").classList.toggle("is-focused", current.focus === lane),
    );
    renderConversation("alpha");
    renderConversation("beta");
    renderMemory(current.memory);
    const publicContext =
      mode === "protected" &&
      steps()
        .slice(0, step + 1)
        .some((event) =>
          event.messages.some((message) =>
            message.title.startsWith("Fresh context"),
          ),
        );
    $("beta-context").textContent = publicContext
      ? "Fresh publishing run · public sources only"
      : "Customer Beta · troubleshooting note";
    $("beta-permissions").textContent = publicContext
      ? "Can read public sources only"
      : "Can read Beta + public sources";
    if (mode === "protected" && step >= 6) {
      const record = publicContext ? $("public-record") : $("private-record");
      if (record)
        $("memory-content").scrollTop =
          record.offsetTop - $("memory-content").offsetTop;
    }
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
          ? "BOUNDARY EXPOSED"
          : "ILLUSTRATED";
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
  function populateEvidence() {
    const evidence = window.DEMO_EVIDENCE;
    const valid =
      evidence &&
      evidence.verified === true &&
      Array.isArray(evidence.events) &&
      evidence.events.length &&
      evidence.events.every(
        (event) =>
          /^[a-f0-9]{32}$/.test(event.traceId) &&
          ["ALLOW", "DENY", "QUARANTINE"].includes(event.decision) &&
          [event.gateMs, event.sensoMs, event.totalMs].every(
            (value) =>
              typeof value === "number" && Number.isFinite(value) && value >= 0,
          ),
      );
    if (!valid) {
      $("evidence-status").textContent =
        "Verified metadata is unavailable. No measurements are shown.";
      return;
    }
    $("trace-count").textContent = String(evidence.events.length);
    $("allow-count").textContent = String(
      evidence.events.filter((event) => event.decision === "ALLOW").length,
    );
    $("blocked-count").textContent = String(
      evidence.events.filter((event) => event.decision === "QUARANTINE").length,
    );
    $("evidence-status").textContent =
      "Seven protected AWS gateway traces verified by a ClickHouse read-back. These records substantiate decisions and timings from an earlier run; they are not a transcript of the illustrated poisoned-skill story.";
    evidence.events.forEach((event) => {
      const row = node("tr");
      const operation = node("td", "", event.label);
      operation.append(node("small", "", event.traceId));
      const decision = node("td");
      decision.append(
        node(
          "span",
          "decision decision-" + event.decision.toLowerCase(),
          event.decision,
        ),
        node("small", "", event.reason),
      );
      row.append(operation, decision);
      [event.gateMs, event.sensoMs, event.totalMs].forEach((value) =>
        row.append(node("td", "", value.toFixed(1) + " ms")),
      );
      $("evidence-rows").append(row);
    });
    const date = new Date(evidence.recordedAt);
    $("evidence-time").textContent = Number.isNaN(date.valueOf())
      ? ""
      : "Read-back verified " +
        date.toLocaleString("en-US", {
          timeZone: "America/Los_Angeles",
          dateStyle: "medium",
          timeStyle: "short",
        }) +
        " Pacific.";
    const limits = [
      "The conversations, poisoned skill, memory snapshots, model portrayal, and response bodies are authored illustrations. Haiku did not execute this script.",
      "The earlier unprotected comparison used a local mock. These receipts cover only the protected AWS gateway.",
      "Actual publication was allowed in the recorded run; that flow read its public source directly. These rows do not establish the illustrated public-memory retrieval or provide a public report URL.",
      "ClickHouse contains metadata only. It does not store the displayed chats, credentials, queries, or customer records.",
      "Replay with gateway starts a fresh illustrated run. It cannot revoke information already returned to an agent, and it does not toggle production policy.",
    ];
    limits.forEach((text) => $("evidence-limits").append(node("li", "", text)));
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
