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
