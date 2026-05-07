const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const state = {
  hasKey: false,
  logs: [],
  filter: "",
  level: "ALL",
  eventSource: null,
};

const levelRank = {
  DEBUG: 10,
  INFO: 20,
  WARNING: 30,
  ERROR: 40,
  CRITICAL: 50,
};

function byId(id) {
  return document.getElementById(id);
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 2500) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(id);
  }
}

async function waitForStatus(timeoutMs = 15000) {
  const loadingText = document.querySelector("#loading p");
  let attempts = 0;
  const deadline = Date.now() + timeoutMs;

  while (true) {
    attempts += 1;
    try {
      const url = new URL("/status", window.location.origin);
      url.searchParams.set("_", Date.now().toString());
      const resp = await fetchWithTimeout(url);
      if (resp.ok) return await resp.json();
    } catch (e) {
      // The settings server can need a few seconds while the robot app starts.
    }

    if (loadingText) {
      loadingText.textContent = attempts > 8 ? "Starting backend" : "Loading";
    }
    if (Date.now() >= deadline) return null;
    await sleep(500);
  }
}

async function validateKey(key) {
  const resp = await fetch("/validate_api_key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ openai_api_key: key }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.error || "validation_failed");
  }
  return data;
}

async function saveSettings(settings) {
  const resp = await fetch("/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error || "save_failed");
  }
  return await resp.json();
}

function show(el, flag) {
  if (el) el.classList.toggle("hidden", !flag);
}

function setTone(el, tone, text) {
  if (!el) return;
  el.className = el.className
    .split(" ")
    .filter((name) => !["ok", "warn", "error", "pending"].includes(name))
    .join(" ");
  if (tone) el.classList.add(tone);
  if (text !== undefined) el.textContent = text;
}

function setText(id, text) {
  const el = byId(id);
  if (el) el.textContent = text;
}

function applyStatus(st) {
  state.hasKey = Boolean(st.has_key);
  const wakeReady = Boolean(st.wake_detector_ready);
  const wakeEnabled = st.wake_enabled !== false;
  const relayUrl = st.codex_relay_url || "http://127.0.0.1:8766";
  const workspace = st.codex_default_workspace || "current";
  const languages = st.realtime_languages || "English";
  const primaryLanguage = languages.split(",")[0].trim() || "English";

  setText("metric-key", state.hasKey ? "Ready" : "Missing");
  setText("metric-voice", st.realtime_voice || "cedar");
  setText("metric-language-detail", `Primary ${primaryLanguage}`);
  setText("metric-relay", relayUrl.replace(/^https?:\/\//, ""));
  setText("metric-relay-detail", `Workspace ${workspace}`);

  if (!wakeEnabled) {
    setText("metric-wake", "Disabled");
    setText("metric-wake-detail", "Realtime starts immediately");
  } else if (wakeReady) {
    setText("metric-wake", "Ready");
    setText("metric-wake-detail", st.wake_phrase || "HEY ROBO");
  } else {
    setText("metric-wake", "Needs model");
    setText("metric-wake-detail", st.wake_engine || "vosk");
  }

  const runtimeReady = state.hasKey && (!wakeEnabled || wakeReady);
  setTone(
    byId("connection-state"),
    runtimeReady ? "ok" : "warn",
    runtimeReady ? "Ready" : state.hasKey ? "Wake setup" : "Needs key",
  );
  setTone(byId("settings-chip"), state.hasKey ? "ok" : "warn", state.hasKey ? "Configured" : "Required");

  byId("wake-enabled").checked = wakeEnabled;
  byId("wake-phrase").value = st.wake_phrase || "HEY ROBO";
  byId("wake-timeout").value = st.wake_session_timeout_seconds || 45;
  byId("wake-model-path").value = st.wake_model_path || "";
  byId("voice").value = st.realtime_voice || "cedar";
  byId("languages").value = languages;
  byId("relay-url").value = relayUrl;
  byId("workspace-id").value = workspace;

  const wakeStatus = byId("wake-status");
  wakeStatus.textContent = st.wake_status || "Wake detector status unavailable.";
  wakeStatus.className = wakeReady ? "status ok" : wakeEnabled ? "status warn" : "status";

  const relayToken = byId("relay-token");
  relayToken.placeholder = st.has_codex_relay_token ? "Token saved. Leave blank to keep it." : "Paste relay token";

  show(byId("configured"), state.hasKey);
  show(byId("form-panel"), true);
}

function formatTime(timestamp) {
  try {
    return new Date(timestamp).toLocaleTimeString([], {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch (e) {
    return "--:--:--";
  }
}

function renderLogs() {
  const logWindow = byId("log-window");
  if (!logWindow) return;

  const minRank = state.level === "ALL" ? 0 : levelRank[state.level] || 0;
  const filter = state.filter.trim().toLowerCase();
  const filtered = state.logs.filter((event) => {
    const level = event.level || "INFO";
    const rank = levelRank[level] || 0;
    if (rank < minRank) return false;
    if (!filter) return true;
    return `${event.message || ""} ${event.logger || ""} ${event.source || ""}`.toLowerCase().includes(filter);
  });

  logWindow.replaceChildren();
  if (filtered.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-log";
    empty.textContent = state.logs.length ? "No logs match the filter" : "Waiting for app events";
    logWindow.append(empty);
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const event of filtered.slice(-400)) {
    const row = document.createElement("div");
    row.className = `log-row level-${String(event.level || "info").toLowerCase()}`;

    const time = document.createElement("span");
    time.className = "log-time";
    time.textContent = formatTime(event.timestamp);

    const level = document.createElement("span");
    level.className = "log-level";
    level.textContent = event.level || "INFO";

    const source = document.createElement("span");
    source.className = "log-source";
    source.textContent = event.source || event.logger || "hey_robo";

    const message = document.createElement("span");
    message.className = "log-message";
    message.textContent = event.message || "";

    row.append(time, level, source, message);
    fragment.append(row);
  }

  const shouldStick = logWindow.scrollTop + logWindow.clientHeight >= logWindow.scrollHeight - 80;
  logWindow.append(fragment);
  if (shouldStick) {
    logWindow.scrollTop = logWindow.scrollHeight;
  }
}

function addLogEvent(event) {
  if (!event || !event.message) return;
  state.logs.push(event);
  if (state.logs.length > 500) {
    state.logs.splice(0, state.logs.length - 500);
  }
  renderLogs();
}

async function loadRecentLogs() {
  try {
    const resp = await fetchWithTimeout("/logs/recent?limit=200");
    if (!resp.ok) return;
    const data = await resp.json();
    state.logs = Array.isArray(data.events) ? data.events : [];
    renderLogs();
  } catch (e) {
    setTone(byId("log-state"), "warn", "Buffered only");
  }
}

function connectLiveLogs() {
  const logState = byId("log-state");
  if (!("EventSource" in window)) {
    setTone(logState, "warn", "SSE unavailable");
    return;
  }

  if (state.eventSource) {
    state.eventSource.close();
  }

  const source = new EventSource("/logs/events");
  state.eventSource = source;

  source.addEventListener("ready", () => {
    setTone(logState, "ok", "Live");
  });
  source.onopen = () => {
    setTone(logState, "ok", "Live");
  };
  source.onmessage = (event) => {
    try {
      addLogEvent(JSON.parse(event.data));
    } catch (e) {
      // Ignore malformed log events.
    }
  };
  source.onerror = () => {
    setTone(logState, "warn", "Reconnecting");
  };
}

function wireLogControls() {
  byId("log-filter").addEventListener("input", (event) => {
    state.filter = event.target.value || "";
    renderLogs();
  });
  byId("log-level").addEventListener("change", (event) => {
    state.level = event.target.value || "ALL";
    renderLogs();
  });
  byId("clear-log-btn").addEventListener("click", () => {
    state.logs = [];
    renderLogs();
  });
}

function collectSettings() {
  const key = byId("api-key").value.trim();
  return {
    key,
    payload: {
      openai_api_key: key || undefined,
      wake_enabled: byId("wake-enabled").checked,
      wake_phrase: byId("wake-phrase").value.trim(),
      wake_model_path: byId("wake-model-path").value.trim() || undefined,
      wake_session_timeout_seconds: Number.parseFloat(byId("wake-timeout").value) || 45,
      realtime_voice: byId("voice").value,
      realtime_languages: byId("languages").value.trim() || "English",
      codex_relay_url: byId("relay-url").value.trim(),
      codex_relay_token: byId("relay-token").value.trim() || undefined,
      codex_default_workspace: byId("workspace-id").value.trim(),
    },
  };
}

function wireSettings() {
  const statusEl = byId("status");
  const saveBtn = byId("save-btn");
  const apiKeyInput = byId("api-key");

  byId("change-key-btn").addEventListener("click", () => {
    apiKeyInput.value = "";
    apiKeyInput.focus();
    statusEl.textContent = "";
    statusEl.className = "status";
  });

  apiKeyInput.addEventListener("input", () => {
    apiKeyInput.classList.remove("error");
  });

  saveBtn.addEventListener("click", async () => {
    const { key, payload } = collectSettings();
    if (!key && !state.hasKey) {
      statusEl.textContent = "Enter a valid OpenAI key.";
      statusEl.className = "status warn";
      apiKeyInput.classList.add("error");
      return;
    }

    saveBtn.disabled = true;
    statusEl.textContent = key ? "Validating API key" : "Saving settings";
    statusEl.className = "status";
    apiKeyInput.classList.remove("error");

    try {
      if (key) {
        const validation = await validateKey(key);
        if (!validation.valid) {
          throw new Error(validation.error || "invalid_api_key");
        }
      }

      await saveSettings(payload);
      statusEl.textContent = "Saved";
      statusEl.className = "status ok";
      const refreshed = await waitForStatus(3000);
      if (refreshed) applyStatus(refreshed);
      apiKeyInput.value = "";
      byId("relay-token").value = "";
    } catch (e) {
      apiKeyInput.classList.add("error");
      statusEl.textContent =
        e.message === "invalid_api_key" ? "Invalid API key. Check the key and try again." : "Failed to save settings.";
      statusEl.className = "status error";
    } finally {
      saveBtn.disabled = false;
    }
  });
}

async function init() {
  const loading = byId("loading");
  show(loading, true);
  show(byId("form-panel"), false);

  wireLogControls();
  wireSettings();
  await loadRecentLogs();
  connectLiveLogs();

  const st = (await waitForStatus()) || { has_key: false };
  applyStatus(st);
  show(loading, false);
}

window.addEventListener("DOMContentLoaded", init);
