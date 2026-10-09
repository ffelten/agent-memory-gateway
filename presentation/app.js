"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const scenes = [
    {
      kicker: "01 / THE BOUNDARY",
      title: "Different customers. Different access.",
      story:
        "Beta works for a different customer. The first request is correctly denied.",
      baseline: {
        description: "Beta’s own credential cannot open Alpha’s record.",
        memory: "Own credential",
        detail: "Beta access only",
        icon: "key",
        recordIcon: "lock",
        recordDetail: "Private customer data",
        fragment: "Access belongs to a customer, not to shared memory.",
        result: "Access denied",
        resultIcon: "lock",
        kicker: "CUSTOMER API",
        code: "403",
        codeLabel: "FORBIDDEN",
      },
      protected: {
        description: "The same customer boundary applies to every agent.",
        agent: "Agent Beta",
        agentDetail: "Customer Beta",
        memory: "Permission check",
        detail: "Identity stays scoped",
        icon: "shield",
        record: "Alpha’s record",
        recordIcon: "lock",
        recordDetail: "Private customer data",
        fragment: "The gateway checks the caller’s source permissions.",
        result: "Access denied",
        resultIcon: "lock",
        kicker: "CUSTOMER API",
        code: "403",
        codeLabel: "FORBIDDEN",
      },
    },
    {
      kicker: "02 / THE LEAK",
      title: "A helpful note carries a working key.",
      story:
        "The handoff preserved incident context. In the baseline, Beta retrieves the copied credential and opens Alpha’s record.",
      baseline: {
        description:
          "The copied credential gives Beta another customer’s access.",
        memory: "Shared handoff",
        detail: "Credential copied",
        icon: "memory",
        recordIcon: "unlock",
        recordDetail: "Synthetic record exposed",
        fragment: "Incident handoff → credential •••• → shared memory",
        result: "Alpha record exposed",
        resultIcon: "unlock",
        kicker: "CUSTOMER API",
        code: "200",
        codeLabel: "ACCESS GRANTED",
      },
      protected: {
        description: "Replay the same candidate through the protected path.",
        agent: "Agent Beta",
        agentDetail: "Customer Beta",
        memory: "Same handoff",
        detail: "Admission comes first",
        icon: "document",
        record: "Alpha’s record",
        recordIcon: "lock",
        recordDetail: "Customer boundary intact",
        fragment: "Same incident. Same handoff. One admission decision.",
        result: "Boundary stays intact",
        resultIcon: "lock",
        kicker: "CUSTOMER ACCESS",
        code: "403",
        codeLabel: "FORBIDDEN",
      },
    },
    {
      kicker: "03 / THE PROTECTION",
      title: "Stop the leak before memory is shared.",
      story:
        "The entire credential-bearing write is quarantined. Beta cannot retrieve that memory—even with its direct ID.",
      baseline: {
        description:
          "The baseline keeps the leaked credential in shared memory.",
        memory: "Shared handoff",
        detail: "Credential retrieved",
        icon: "memory",
        recordIcon: "unlock",
        recordDetail: "Synthetic record exposed",
        fragment: "Stored → retrieved → customer access inherited",
        result: "Alpha record exposed",
        resultIcon: "unlock",
        kicker: "CUSTOMER API",
        code: "200",
        codeLabel: "ACCESS GRANTED",
      },
      protected: {
        description: "The write is quarantined before Senso ingestion.",
        agent: "Agent Beta",
        agentDetail: "Customer Beta",
        memory: "Quarantined",
        detail: "Credential detected",
        icon: "shield",
        record: "Private memory",
        recordIcon: "lock",
        recordDetail: "Unavailable to Beta",
        fragment: "SECRET_MATCH · whole write quarantined",
        result: "Private memory denied",
        resultIcon: "shield",
        kicker: "BETA’S DIRECT MEMORY LOOKUP",
        code: "404",
        codeLabel: "NOT READABLE",
      },
    },
    {
      kicker: "04 / USEFUL WORK",
      title: "Keep the knowledge. Keep the boundary.",
      story:
        "Alpha retains approved private access. A fresh public-only context publishes useful sourced work. The unsafe handoff stays quarantined.",
      baseline: {
        description: "One shared note was enough to cross a customer boundary.",
        memory: "Shared handoff",
        detail: "Credential retrieved",
        icon: "memory",
        recordIcon: "unlock",
        recordDetail: "Synthetic record exposed",
        fragment: "Shared context became another customer’s access.",
        result: "Alpha record exposed",
        resultIcon: "unlock",
        kicker: "CUSTOMER API",
        code: "200",
        codeLabel: "ACCESS GRANTED",
      },
      protected: {
        description:
          "A separate public-only context completes an allowed task.",
        agent: "Agent Beta",
        agentDetail: "Fresh public-only run",
        memory: "Public runbook",
        detail: "Approved source",
        icon: "document",
        record: "Published note",
        recordIcon: "check",
        recordDetail: "Source linked",
        fragment: "Public source → useful note → published page",
        result: "Useful work completed",
        resultIcon: "check",
        kicker: "PUBLIC REPORT",
        code: "200",
        codeLabel: "PUBLISHED",
      },
    },
  ];

  let active = 0;
  let playing = false;
  let timer = null;
  let toastTimer = null;
  let previousFocus = null;
  const sceneDuration = 9500;
  const chapterButtons = [...document.querySelectorAll("[data-scene]")];

  function text(id, value) {
    $(id).textContent = value;
  }
  function icon(id, name) {
    $(id)
      .querySelector("use")
      .setAttribute("href", "#i-" + name);
  }
  function response(id, code, label) {
    $(id).replaceChildren(document.createTextNode(code));
    const detail = document.createElement("span");
    detail.textContent = label;
    $(id).append(detail);
  }
  function renderLane(prefix, data) {
    text(prefix + "-description", data.description);
    text(prefix + "-memory-label", data.memory);
    text(prefix + "-memory-detail", data.detail);
    icon(prefix + "-memory-icon", data.icon);
    icon(prefix + "-record-icon", data.recordIcon);
    text(prefix + "-record-detail", data.recordDetail);
    text(prefix + "-fragment-text", data.fragment);
    text(prefix + "-result-title", data.result);
    text(prefix + "-result-kicker", data.kicker);
    icon(prefix + "-result-icon", data.resultIcon);
    response(prefix + "-code", data.code, data.codeLabel);
    if (prefix === "protected") {
      text("protected-agent", data.agent);
      text("protected-agent-detail", data.agentDetail);
      text("protected-record-label", data.record);
    }
  }
  function renderScene(index) {
    active = Math.max(0, Math.min(scenes.length - 1, index));
    const scene = scenes[active];
    document.body.dataset.activeScene = String(active);
    text("scene-kicker", scene.kicker);
    text("scene-title", scene.title);
    text("story-line", scene.story);
    $("incident-stage").setAttribute("aria-labelledby", "chapter-" + active);
    chapterButtons.forEach((button, i) => {
      button.classList.toggle("is-active", i === active);
      button.classList.toggle("is-complete", i < active);
      button.setAttribute("aria-selected", String(i === active));
      button.tabIndex = i === active ? 0 : -1;
    });
    renderLane("baseline", scene.baseline);
    renderLane("protected", scene.protected);
    $("baseline-lane").classList.toggle("is-exposed", active > 0);
    $("protected-lane").classList.toggle("is-protected", active >= 2);
    $("protected-lane").classList.toggle("is-useful", active === 3);
    $("prev").disabled = active === 0;
    $("next").disabled = active === scenes.length - 1;
    response("scene-count", String(active + 1).padStart(2, "0"), "/ 04");
  }
  function setPlaying(value) {
    playing = value;
    document.body.classList.toggle("is-playing", playing);
    $("replay").setAttribute("aria-pressed", String(playing));
    $("stage-play").setAttribute("aria-pressed", String(playing));
    $("stage-play").setAttribute(
      "aria-label",
      playing ? "Pause recorded replay" : "Play recorded replay",
    );
    text("play-label", playing ? "Pause the replay" : "Replay the incident");
    icon("play-glyph", playing ? "pause" : "play");
    icon("stage-play-glyph", playing ? "pause" : "play");
    window.clearTimeout(timer);
    timer = null;
    if (playing) timer = window.setTimeout(advanceReplay, sceneDuration);
  }
  function advanceReplay() {
    if (!playing) return;
    if (active === scenes.length - 1) {
      setPlaying(false);
      return;
    }
    renderScene(active + 1);
    timer = window.setTimeout(advanceReplay, sceneDuration);
  }
  function navigate(index, focusTab = false) {
    setPlaying(false);
    renderScene(index);
    if (focusTab) chapterButtons[active].focus();
  }
  function toggleReplay(scrollToStory = false) {
    if (playing) {
      setPlaying(false);
      return;
    }
    if (active === scenes.length - 1) renderScene(0);
    setPlaying(true);
    if (scrollToStory && !document.fullscreenElement) {
      document.querySelector(".chapter-tabs").scrollIntoView({
        block: "start",
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "instant"
          : "smooth",
      });
    }
  }

  chapterButtons.forEach((button) =>
    button.addEventListener("click", () =>
      navigate(Number(button.dataset.scene)),
    ),
  );
  $("next").addEventListener("click", () => navigate(active + 1));
  $("prev").addEventListener("click", () => navigate(active - 1));
  $("replay").addEventListener("click", () => toggleReplay(true));
  $("stage-play").addEventListener("click", () => toggleReplay());
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) setPlaying(false);
  });

  const dialog = $("evidence-dialog");
  function openEvidence() {
    setPlaying(false);
    previousFocus = document.activeElement;
    dialog.showModal();
    $("close-evidence").focus();
  }
  $("evidence-button").addEventListener("click", openEvidence);
  $("receipts-secondary").addEventListener("click", openEvidence);
  $("close-evidence").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    if (previousFocus && document.contains(previousFocus))
      previousFocus.focus();
  });
  dialog.addEventListener("click", (event) => {
    const box = dialog.getBoundingClientRect();
    if (
      event.target === dialog &&
      (event.clientX < box.left ||
        event.clientX > box.right ||
        event.clientY < box.top ||
        event.clientY > box.bottom)
    )
      dialog.close();
  });
  document.addEventListener("keydown", (event) => {
    if (
      event.altKey ||
      event.ctrlKey ||
      event.metaKey ||
      dialog.open ||
      event.target.closest('input, textarea, select, [contenteditable="true"]')
    )
      return;
    const onTab = event.target.matches("[data-scene]");
    const onControl = event.target.closest("button, a");
    if (
      (event.key === "ArrowRight" || event.key === "ArrowLeft") &&
      (!onControl || onTab)
    ) {
      event.preventDefault();
      navigate(active + (event.key === "ArrowRight" ? 1 : -1), onTab);
    } else if (event.code === "Space" && !onControl) {
      event.preventDefault();
      toggleReplay();
    } else if (onTab && (event.key === "Home" || event.key === "End")) {
      event.preventDefault();
      navigate(event.key === "Home" ? 0 : scenes.length - 1, true);
    }
  });

  function toast(message) {
    text("toast", message);
    $("toast").hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      $("toast").hidden = true;
    }, 4000);
  }
  $("fullscreen").addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (document.documentElement.requestFullscreen)
        await document.documentElement.requestFullscreen();
      else toast("Full screen is unavailable in this browser.");
    } catch (_) {
      toast("Use your browser’s full-screen control to present.");
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
  });

  function validEvidence(value) {
    return (
      value &&
      value.verified === true &&
      Array.isArray(value.events) &&
      value.events.length > 0 &&
      value.events.every(
        (event) =>
          typeof event.label === "string" &&
          /^[a-f0-9]{32}$/.test(event.traceId) &&
          ["ALLOW", "DENY", "QUARANTINE"].includes(event.decision) &&
          [event.gateMs, event.sensoMs, event.totalMs].every(
            (number) =>
              typeof number === "number" &&
              Number.isFinite(number) &&
              number >= 0,
          ),
      )
    );
  }
  function populateEvidence() {
    const evidence = window.DEMO_EVIDENCE;
    if (!validEvidence(evidence)) {
      text(
        "evidence-status",
        "Verified event metadata is unavailable. No measured values are shown.",
      );
      text("trace-count", "—");
      text("allow-count", "—");
      text("blocked-count", "—");
      return;
    }
    text("trace-count", String(evidence.events.length));
    text(
      "allow-count",
      String(
        evidence.events.filter((event) => event.decision === "ALLOW").length,
      ),
    );
    text(
      "blocked-count",
      String(
        evidence.events.filter((event) => event.decision === "QUARANTINE")
          .length,
      ),
    );
    const badges = [
      "Verified ClickHouse read-back",
      "Protected AWS run",
      "Metadata only",
    ];
    for (const label of badges) {
      const element = document.createElement("span");
      element.textContent = label;
      $("evidence-status").append(element);
    }
    const format = (value) => value.toFixed(value < 10 ? 2 : 1) + " ms";
    for (const event of evidence.events) {
      const row = document.createElement("tr");
      const labelCell = document.createElement("td");
      labelCell.textContent = event.label;
      const trace = document.createElement("small");
      trace.textContent = event.traceId;
      labelCell.append(trace);
      const decisionCell = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = "decision decision-" + event.decision.toLowerCase();
      badge.textContent = event.decision;
      decisionCell.append(badge);
      if (typeof event.reason === "string") {
        const reason = document.createElement("small");
        reason.textContent = event.reason;
        decisionCell.append(reason);
      }
      row.append(labelCell, decisionCell);
      for (const number of [event.gateMs, event.sensoMs, event.totalMs]) {
        const cell = document.createElement("td");
        cell.textContent = format(number);
        row.append(cell);
      }
      $("evidence-rows").append(row);
    }
    const limitations = [
      "The incident flow is an illustration of recorded outcomes. Replay sends no requests and cannot disable the gateway.",
      "The unprotected comparison is a local mock baseline. These ClickHouse receipts cover the protected AWS run.",
      "Quarantine applies to the entire unsafe candidate. The useful public note uses a separately approved public source.",
      "Publication was observed; the current publishing flow reads its public source directly. This is not proof of a shared-memory retrieval step.",
      "These named fixtures do not establish universal secret detection or agent runtime isolation.",
    ];
    for (const item of limitations) {
      const li = document.createElement("li");
      li.textContent = item;
      $("evidence-limits").append(li);
    }
    const date = new Date(evidence.recordedAt);
    text(
      "evidence-time",
      Number.isNaN(date.valueOf())
        ? "Verification time unavailable."
        : "ClickHouse read-back verified " +
            date.toLocaleString("en-US", {
              timeZone: "America/Los_Angeles",
              dateStyle: "medium",
              timeStyle: "short",
            }) +
            " Pacific.",
    );
    if (typeof evidence.reportUrl === "string") {
      try {
        const url = new URL(evidence.reportUrl);
        if (
          url.origin ===
            "https://yrx2yjzb7h.execute-api.us-east-1.amazonaws.com" &&
          /^\/reports\/[a-f0-9]{32}$/.test(url.pathname) &&
          !url.search &&
          !url.hash
        ) {
          $("report-link").href = url.href;
          $("report-link").hidden = false;
        }
      } catch (_) {
        /* An absent verified URL simply leaves the link hidden. */
      }
    }
  }
  renderScene(0);
  populateEvidence();
})();
