const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchWithTimeout(url, options = {}, timeoutMs = 2000) {
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
      const resp = await fetchWithTimeout(url, {}, 2000);
      if (resp.ok) return await resp.json();
    } catch (e) {}
    if (loadingText) {
      loadingText.textContent = attempts > 8 ? "Starting backend…" : "Loading…";
    }
    if (Date.now() >= deadline) return null;
    await sleep(500);
  }
}

async function validateKey(key) {
  const body = { openai_api_key: key };
  const resp = await fetch("/validate_api_key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.error || "validation_failed");
  }
  return data;
}

async function saveKey(key) {
  const body = { openai_api_key: key };
  const resp = await fetch("/openai_api_key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error || "save_failed");
  }
  return await resp.json();
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
  el.classList.toggle("hidden", !flag);
}

async function init() {
  const loading = document.getElementById("loading");
  const statusEl = document.getElementById("status");
  const formPanel = document.getElementById("form-panel");
  const configuredPanel = document.getElementById("configured");
  const saveBtn = document.getElementById("save-btn");
  const changeKeyBtn = document.getElementById("change-key-btn");
  const input = document.getElementById("api-key");
  const wakeEnabled = document.getElementById("wake-enabled");
  const wakePhrase = document.getElementById("wake-phrase");
  const wakeTimeout = document.getElementById("wake-timeout");
  const wakeModelPath = document.getElementById("wake-model-path");
  const wakeStatus = document.getElementById("wake-status");
  const voice = document.getElementById("voice");
  const relayUrl = document.getElementById("relay-url");
  const relayToken = document.getElementById("relay-token");
  const workspaceId = document.getElementById("workspace-id");

  show(loading, true);
  show(formPanel, false);
  show(configuredPanel, false);

  const st = (await waitForStatus()) || { has_key: false };

  wakeEnabled.checked = st.wake_enabled !== false;
  wakePhrase.value = st.wake_phrase || "HEY ROBO";
  wakeTimeout.value = st.wake_session_timeout_seconds || 45;
  wakeModelPath.value = st.wake_model_path || "";
  wakeStatus.textContent = st.wake_status || "Wake detector status unavailable.";
  wakeStatus.className = st.wake_detector_ready ? "status ok" : "status warn";
  voice.value = st.realtime_voice || "cedar";
  relayUrl.value = st.codex_relay_url || "http://127.0.0.1:8766";
  workspaceId.value = st.codex_default_workspace || "current";
  relayToken.placeholder = st.has_codex_relay_token
    ? "Token saved. Leave blank to keep it."
    : "Paste relay token";

  if (st.has_key) {
    show(configuredPanel, true);
    show(formPanel, true);
  } else {
    show(formPanel, true);
  }
  show(loading, false);

  changeKeyBtn.addEventListener("click", () => {
    show(configuredPanel, false);
    show(formPanel, true);
    input.value = "";
    statusEl.textContent = "";
    statusEl.className = "status";
  });

  input.addEventListener("input", () => {
    input.classList.remove("error");
  });

  saveBtn.addEventListener("click", async () => {
    const key = input.value.trim();
    if (!key) {
      if (!st.has_key) {
        statusEl.textContent = "Please enter a valid OpenAI key.";
        statusEl.className = "status warn";
        input.classList.add("error");
        return;
      }
    }
    statusEl.textContent = key ? "Validating API key..." : "Saving settings...";
    statusEl.className = "status";
    input.classList.remove("error");
    try {
      if (key) {
        const validation = await validateKey(key);
        if (!validation.valid) {
          statusEl.textContent = "Invalid API key. Please check your key and try again.";
          statusEl.className = "status error";
          input.classList.add("error");
          return;
        }
      }
      statusEl.textContent = "Saving...";
      statusEl.className = "status ok";
      await saveSettings({
        openai_api_key: key || undefined,
        wake_enabled: wakeEnabled.checked,
        wake_phrase: wakePhrase.value.trim(),
        wake_model_path: wakeModelPath.value.trim() || undefined,
        wake_session_timeout_seconds: Number.parseFloat(wakeTimeout.value) || 45,
        realtime_voice: voice.value,
        codex_relay_url: relayUrl.value.trim(),
        codex_relay_token: relayToken.value.trim() || undefined,
        codex_default_workspace: workspaceId.value.trim(),
      });
      statusEl.textContent = "Saved. Reloading…";
      statusEl.className = "status ok";
      window.location.reload();
    } catch (e) {
      input.classList.add("error");
      if (e.message === "invalid_api_key") {
        statusEl.textContent = "Invalid API key. Please check your key and try again.";
      } else {
        statusEl.textContent = "Failed to validate/save key. Please try again.";
      }
      statusEl.className = "status error";
    }
  });
}

window.addEventListener("DOMContentLoaded", init);
