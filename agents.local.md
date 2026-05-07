# Reachy Mini Local Configuration

## Setup Status

Setup complete: YES

## User Environment

- Robot type: Reachy Mini Wireless over WiFi
- OS: macOS
- Shell: zsh
- Python env tool: uv
- Resources path: `/Users/adrian/reachy_mini_resources/`
- Resources venv: `/Users/adrian/reachy_mini_resources/.venv`
- App name/slug: `HeyRobo`
- App path: `/Users/adrian/Documents/GitHub/reachy-assistant/hey_robo`
- Python package name: `hey_robo`
- Publishing: public after local/hardware testing works

## Tool Check

- `git`: available
- `python3`: available
- `uv`: available
- `hf`: available in resources venv
- `reachy-mini-app-assistant`: available in resources venv
- `codex`: available on this Mac

## Notes for Future Sessions

- User wants a mixed Reachy Mini assistant: local wake phrase first, then OpenAI Realtime voice agent, with tool dispatch to a Codex-capable machine on WiFi.
- Initial wake phrase requested: `HEY ROBO`.
- Wake phrase must be personalized through app settings.
- App settings should also manage API keys, local Codex relay configuration, and Realtime voice selection.
- Codex relay will run on this Mac and may let Codex edit automatically only on a branch.
- Official conversation-template scaffold is complete in `hey_robo/`.
- `git-lfs` is not installed; reference repo media assets were not fully fetched, but source files are available.
- Implemented app settings fields, local Codex relay, branch-before-edit relay behavior, and `dispatch_codex_task` Realtime tool.
- Implemented Vosk-backed local wake phrase gating. Realtime starts only after wake detection when `HEY_ROBO_WAKE_ENABLED=true`.
- Wake model must be configured with `HEY_ROBO_WAKE_MODEL_PATH` or placed in the app instance under `wake_models/vosk-model-small-en-us-0.15`.
- Still pending: physical robot test with an installed Vosk model and the Reachy Mini microphone.
