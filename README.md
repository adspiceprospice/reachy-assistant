---
title: Hey Robo
emoji: 🤖
colorFrom: green
colorTo: gray
sdk: static
pinned: false
tags:
  - reachy_mini
  - reachy_mini_python_app
---

# Hey Robo

Made by Adrian with Codex - [curiosityai.nl](https://curiosityai.nl)

Hey Robo is a Reachy Mini Wireless assistant app forked from the official
conversation template. It listens locally for a configurable wake phrase,
starts an OpenAI Realtime voice session only after wake detection, and can
dispatch authenticated Codex tasks to a relay on the local WiFi network.

This repository root is the Reachy Mini app root. The Python package remains
`hey_robo` under `src/hey_robo`, and the app entrypoint remains `hey_robo`.

## Wake Phrase

Wake gating is enabled by default with `HEY_ROBO_WAKE_ENABLED=true`.

The first production wake backend is Vosk because it can match a personalized
text phrase such as `HEY ROBO` without sending ambient audio to a cloud service.
Configure a local Vosk model directory in app settings or with:

```bash
HEY_ROBO_WAKE_MODEL_PATH=/path/to/vosk-model-small-en-us-0.15
```

If no model is configured, the app does not open Realtime automatically. The
settings page reports the missing model path so it can be fixed before testing.

For local development without wake gating:

```bash
HEY_ROBO_WAKE_ENABLED=false
```

During a live conversation, say something like "go to sleep" or "standby" to
close the active Realtime session and return to local wake-phrase listening.

## Language Preferences

Set one or more Realtime languages in the app settings, ordered by likelihood.
For example:

```bash
HEY_ROBO_REALTIME_LANGUAGES=Dutch, English
```

The first language is used as the strongest startup hint after the wake phrase,
and the full ordered list is included in the Realtime session instructions.

## Codex Relay

The voice agent can call `dispatch_codex_task`, which sends tasks to the local
relay configured in settings. The relay requires a bearer token, allowlisted
workspaces, and creates a `codex/hey-robo-*` branch before running Codex.

Run the relay on the machine that has the Codex CLI installed. When that machine
is not the Reachy Mini itself, bind the relay to `0.0.0.0` and configure Reachy
with the relay machine's WiFi/LAN IP address.

### Start the relay machine

From this repository on the Codex machine:

```bash
python3 -m venv .relay_venv
.relay_venv/bin/python -m pip install fastapi uvicorn pydantic

export HEY_ROBO_CODEX_RELAY_TOKEN="$(openssl rand -hex 24)"
export HEY_ROBO_RELAY_HOST=0.0.0.0
export HEY_ROBO_RELAY_PORT=8766
export HEY_ROBO_CODEX_BINARY="$(command -v codex)"
export HEY_ROBO_CODEX_WORKSPACES="current=/absolute/path/to/a/clean/git/repo"

PYTHONPATH=src .relay_venv/bin/python -m hey_robo.codex_relay
```

`HEY_ROBO_CODEX_WORKSPACES` is an allowlist. Use semicolons for multiple
repositories:

```bash
export HEY_ROBO_CODEX_WORKSPACES="app=/path/to/app;docs=/path/to/docs"
```

The relay refuses to run Codex when the selected workspace has uncommitted
changes. This is intentional: it avoids mixing voice-triggered edits with work
already in progress. Relay logs are written to `~/.hey_robo/codex_tasks` unless
`HEY_ROBO_CODEX_TASK_LOG_DIR` is set.

### Configure Reachy

Find the relay machine's WiFi/LAN IP:

```bash
ipconfig getifaddr en0     # macOS WiFi
hostname -I                # Linux
```

In the Reachy Mini installed app settings, set:

```bash
Codex relay URL: http://<relay-machine-ip>:8766
Codex relay token: <same token as HEY_ROBO_CODEX_RELAY_TOKEN>
Default workspace ID: current
```

Do not use `127.0.0.1` in Reachy settings unless the relay is running on Reachy
itself.

### Smoke test

First test the network path from Reachy:

```bash
curl http://<relay-machine-ip>:8766/health
```

For a safe end-to-end test, allowlist a scratch repository on the relay machine:

```bash
mkdir -p ~/hey-robo-codex-smoke
cd ~/hey-robo-codex-smoke
git init
git config user.email "you@example.com"
git config user.name "HeyRobo Test"
printf "# HeyRobo Codex smoke\n" > README.md
git add README.md
git commit -m "init smoke workspace"

export HEY_ROBO_CODEX_WORKSPACES="smoke=$HOME/hey-robo-codex-smoke"
```

Restart the relay, set Reachy's default workspace ID to `smoke`, then say:

```text
Hey Robo, ask Codex to append one sentence to the README saying the relay smoke test passed.
```

The relay should create a `codex/hey-robo-*` branch in the scratch repository
and the app log window should show the `dispatch_codex_task` call. You can also
test with curl:

```bash
curl -s -X POST "http://<relay-machine-ip>:8766/tasks" \
  -H "Authorization: Bearer <relay-token>" \
  -H "Content-Type: application/json" \
  -d '{"workspace_id":"smoke","task":"Append one sentence to README.md saying the relay smoke test passed."}'
```

## Settings Dashboard

The installed app page lets you manage the OpenAI API key, wake phrase, Vosk
model path, Realtime voice, Realtime language order, Codex relay URL, relay
token, and default workspace. It also includes a live log window for wake
events, Realtime usage, tool calls, Codex relay activity, and errors. API keys
and bearer tokens are redacted before logs are streamed to the browser.

Use the `src/hey_robo/profiles/_hey_robo_locked_profile` folder to customize your own app from this template:
- Edit instructions `_hey_robo_locked_profile/instructions.txt`
- Edit available tools in `_hey_robo_locked_profile/tools.txt`
- You can create your own tools in `_hey_robo_locked_profile` by subclassing the `Tool` class.

Do not forget to customize:
- this `README.md` file
- the `index.html` file (Hugging Face Spaces landing page)
- the `src/hey_robo/static/index.html` (the web app parameters page)

The original README from the conversation app is available in `README_OLD.md`.
