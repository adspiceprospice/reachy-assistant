# Reachy Mini Assistant Plan

## Goal

Create a Reachy Mini app that behaves like a simple voice appliance:

- Idle by default while locally listening for a wake phrase, initially `HEY ROBO`.
- After the wake phrase, start an OpenAI Realtime voice session quickly.
- Let the Realtime agent speak through the robot and use safe robot tools.
- Let the Realtime agent dispatch coding or research tasks to a Codex-capable machine on the same WiFi network.
- Keep the code simple, modular, testable, and safe to run around a physical robot.

## Current Setup Snapshot

- Working directory: `/Users/adrian/Documents/GitHub/reachy-assistant`
- Repository state: empty Git repository, no commits yet.
- App scaffold: `/Users/adrian/Documents/GitHub/reachy-assistant/hey_robo`
- `agents.local.md`: present.
- `/Users/adrian/reachy_mini_resources/`: created with a Python 3.11 `.venv`.
- Available tools checked:
  - `git`: available.
  - `python3`: available.
  - `uv`: available.
  - `hf`: available inside `/Users/adrian/reachy_mini_resources/.venv`.
  - `reachy-mini-app-assistant`: available inside `/Users/adrian/reachy_mini_resources/.venv`.

The official Reachy app guide says Python apps should be created with `reachy-mini-app-assistant`, not by manually creating the app scaffold. The scaffold was created with the official conversation template as `hey_robo`.

Reference resources cloned:

- `/Users/adrian/reachy_mini_resources/reachy_mini`
- `/Users/adrian/reachy_mini_resources/reachy_mini_conversation_app`
- `/Users/adrian/reachy_mini_resources/reachy_mini_dances_library`

Note: `git-lfs` is not installed, so LFS media assets in the first two reference repos were not fully fetched. Normal source files are available.

## Recommended App Flavor

Use a Python Reachy Mini app based on the official `conversation` template, with an optional web UI in `static/`.

Reason:

- The requested product needs microphone/audio handling, a wake-word loop, a Realtime voice session, and LAN service calls.
- The Reachy guide recommends the conversation template for LLM integration, speech, and making the robot talk.
- A JS/static app is good for shareable remote control, but this feature needs a persistent local/on-robot listener and a protected LAN Codex relay.

Confirmed choices:

- Robot hardware: Reachy Mini Wireless over WiFi.
- App name/slug: `HeyRobo`.
- Publishing: develop/test first, then publish publicly after it works.
- Codex relay: this Mac.
- Codex behavior: may edit code automatically, but only on a branch.
- Wake phrase: `HEY ROBO`, configurable in app settings.
- Realtime voice: selectable in app settings.

## Architecture

### Process Overview

```text
Reachy Mini app
  Wake listener
    -> local wake phrase detector
    -> starts/stops Realtime session

  Realtime session
    -> streams robot/user audio to OpenAI Realtime
    -> streams assistant audio back to robot speaker
    -> exposes tool calls

  Robot controller
    -> queues motion/audio actions
    -> executes actions in a control loop
    -> avoids direct motor control from the LLM path

  Codex task client
    -> sends bounded task requests to a LAN Codex relay
    -> polls/streams status
    -> returns short task summaries to the Realtime agent

Codex machine on WiFi
  Codex relay service
    -> authenticated HTTP API
    -> allowlisted workspaces only
    -> invokes `codex exec` non-interactively
    -> records task logs and status
```

### Runtime State Machine

```text
BOOT
  -> IDLE_LISTENING
  -> WAKE_DETECTED
  -> REALTIME_CONNECTING
  -> CONVERSATION_ACTIVE
  -> CODEX_TASK_DISPATCHED
  -> CONVERSATION_ACTIVE
  -> IDLE_LISTENING
```

Failure states should return to `IDLE_LISTENING` with a short spoken or logged explanation.

## Core Components

### 1. Wake Listener

Responsibilities:

- Continuously listen locally for `HEY ROBO` by default.
- Avoid sending always-on ambient audio to cloud services before wake.
- Debounce repeated wake detections.
- Start the Realtime session only after wake.

Implementation shape:

- Define a `WakeDetector` interface.
- Use Vosk as the first production backend so the wake phrase remains app-configurable text.
- Keep a deterministic fake detector for tests.
- Read the wake phrase from app settings so it can be personalized without code edits.
- Require a local Vosk model directory via `HEY_ROBO_WAKE_MODEL_PATH` or an app-instance `wake_models/` folder.

Open question: validate the selected Vosk model on the physical Reachy Mini microphone and tune threshold/timeout if the room is noisy.

### 2. Realtime Voice Session

Responsibilities:

- Connect to OpenAI Realtime using server-side credentials in the Python app process.
- Use the current GA Realtime API shapes, including `gpt-realtime` and `session.update`.
- Handle interruptions and turn-taking.
- Register tools for robot actions and Codex dispatch.
- Read OpenAI API settings and selected voice from app settings.

Tool examples:

- `queue_robot_motion(intent, intensity, duration_seconds)`
- `play_robot_emotion(name)`
- `get_robot_status()`
- `dispatch_codex_task(workspace_id, task, urgency)`

### 3. Robot Controller

Responsibilities:

- Own all Reachy Mini SDK calls.
- Keep LLM tool calls out of direct motor control.
- Use a bounded action queue.
- Clamp or reject unsafe motion requests.
- Keep short, deterministic tool return messages.

This follows the Reachy AI-integration pattern: LLM decides, tool queues an action, and the control loop executes smoothly.

### 4. Codex Relay

Responsibilities:

- Run on this Mac as the Codex-capable machine, not on the robot.
- Expose a small authenticated HTTP API over WiFi:
  - `POST /tasks`
  - `GET /tasks/{id}`
  - optional `GET /tasks/{id}/events`
- Validate a shared bearer token.
- Restrict tasks to an allowlist of workspace IDs.
- Run `codex exec` with safe defaults:
  - explicit working directory
  - `--json` for machine-readable progress
  - `--sandbox workspace-write` unless a workspace opts into another profile
  - timeout and output limits
- Store task status and final summaries.
- Create or switch to a task-specific branch before allowing Codex to edit.

Security baseline:

- No unauthenticated LAN execution.
- No arbitrary workspace paths from the voice agent.
- No direct shell command endpoint.
- No edits on `main`; branch creation is part of the relay contract.
- Treat relay token like a password.
- Prefer Tailscale, WireGuard, or TLS if used beyond a trusted local network.

### 5. App Settings

Responsibilities:

- Store and edit the wake phrase.
- Store API credentials locally, outside committed files.
- Select the OpenAI Realtime voice.
- Configure the local Codex relay URL/token.
- Show whether the app is ready to start listening.

Settings must not commit secrets. Defaults and examples can be committed, but real API keys and relay tokens must stay in a local ignored file or OS keychain.

Implementation status:

- Done: official conversation scaffold.
- Done: settings UI fields for OpenAI API key, wake phrase, Realtime voice, Codex relay URL/token, and default workspace.
- Done: local Codex relay service with bearer-token auth and allowlisted workspaces.
- Done: branch-before-edit behavior for relay tasks.
- Done: Realtime tool `dispatch_codex_task`.
- Done: local Vosk wake detector path and Realtime gating before cloud audio starts.
- Pending: physical robot test with the installed wake model and microphone.

## Evaluation Plan

Use eval-driven development before physical robot testing.

### Code-Based Unit Tests

- Wake detector state transitions.
- Wake debounce.
- Realtime event parsing and tool-call routing.
- Robot action queue validation and clamping.
- Codex relay request validation.
- Codex relay output parsing.
- Settings load/save behavior without writing secrets into committed files.

### Integration Tests With Fakes

- Fake wake phrase starts exactly one Realtime session.
- Fake Realtime tool call queues a robot action.
- Fake Realtime tool call dispatches a Codex task.
- Codex dispatch creates or selects a branch before edit mode.
- Relay unavailable produces a graceful response.
- Ambiguous Codex request asks for clarification instead of executing.
- Disallowed workspace ID is rejected.

### Initial Conversational Eval Cases

Start with 20-50 representative tasks. Initial seed cases:

- Ignore normal speech until `HEY ROBO`.
- Start listening after `HEY ROBO`.
- Stop session after silence or explicit goodbye.
- Ask for clarification before coding if task lacks workspace.
- Dispatch a small Codex task and summarize the result.
- Reject a request to run arbitrary shell commands.
- Reject a request for unsafe robot movement.
- Recover when Codex relay is offline.
- Recover when OpenAI Realtime connection fails.
- Keep robot tool responses concise.
- Preserve user privacy before wake.
- Changing the wake phrase in settings affects the next listening session.
- Changing the voice selector affects the next Realtime session.
- Return to idle after a completed session.

## Implementation Sequence

1. Complete Reachy setup.
   - Install or make available `hf`.
   - Install or make available `reachy-mini-app-assistant`.
   - Clone official reference resources to the chosen permanent path.
   - Record setup status in `agents.local.md`.
   - Status: complete, except `git-lfs` media fetching is not installed.

2. Create official app scaffold.
   - Use `reachy-mini-app-assistant create --template conversation HeyRobo <path>`.
   - Do not publish until local and hardware testing passes.
   - Do not manually create the Python app scaffold.
   - Status: complete as `/Users/adrian/Documents/GitHub/reachy-assistant/hey_robo` using package name `hey_robo`.

3. Add app-level design.
   - Add the wake listener interface.
   - Add a fake wake detector and tests.
   - Add configuration loading.
   - Add settings fields for wake phrase, API keys, Realtime voice, and Codex relay.
   - Status: settings complete; wake detector still pending.

4. Add Realtime session wrapper.
   - Use GA Realtime API events.
   - Keep OpenAI API key server-side only.
   - Register robot and Codex tools.

5. Add robot action queue.
   - Keep Reachy SDK calls in one controller.
   - Add safety validation.
   - Add simulation-friendly tests.

6. Add Codex relay.
   - Implement the LAN HTTP relay separately.
   - Add authentication, workspace allowlist, task timeout, and logs.
   - Add branch-per-task behavior before any edit-capable Codex run.
   - Add fake relay tests before invoking real Codex.
   - Status: first relay implementation complete with focused tests.

7. Add eval harness.
   - Store tasks, transcripts, and grader results.
   - Run a compact eval suite in CI/local checks.

8. Hardware validation.
   - Start with simulation where possible.
   - Then test physical audio, wake phrase, speaker output, and safe motion on the real robot.

## Questions To Answer Before Coding

Please fill these in before implementation:

1. Robot hardware:
   - Answer: Reachy Mini Wireless over WiFi.

2. App name/slug:
   - Answer: `HeyRobo`.

3. Hugging Face publishing:
   - Public, private, or local-only?
   - Answer: public after local and hardware testing confirms it works.

4. Resource path:
   - Is `/Users/adrian/reachy_mini_resources/` OK?
   - Answer: OK.

5. Where should the Codex relay run?
   - Machine hostname/IP: this Mac.
   - Workspace IDs and paths it may access: pending per-project allowlist.

6. Should Codex tasks be allowed to edit code automatically, or should they only produce plans until approved?
   - Answer: edit code automatically on a branch.

7. Wake phrase:
   - Keep `Hey Robot`, or use another phrase?
   - Answer: default `HEY ROBO`; make it configurable in app settings.

8. Realtime voice:
   - Preferred voice, if any:
   - Answer: provide a voice selector in app settings.

## References Read For This Plan

- Reachy Mini `AGENTS.md`
- Reachy Mini `skills/setup-environment.md`
- Reachy Mini `skills/create-app.md`
- Reachy Mini `skills/ai-integration.md`
- Reachy Mini `skills/rest-api.md`
- Reachy Mini `skills/testing-apps.md`
- Reachy Mini Python SDK docs
- OpenAI Realtime/voice-agent docs
- OpenAI Codex CLI docs

## Verification

- `reachy-mini-app-assistant check /Users/adrian/Documents/GitHub/reachy-assistant/hey_robo`: passed.
- `ruff check` on touched modules/tests: passed.
- `pytest tests/test_hey_robo_settings.py tests/tools/test_codex_task.py tests/test_codex_relay.py`: 5 passed.
