"""Bidirectional local audio stream with optional settings UI.

In headless mode, there is no Gradio UI. If the OpenAI API key is not
available via environment/.env, we expose a minimal settings page via the
Reachy Mini Apps settings server to let non-technical users enter it.

The settings UI is served from this package's ``static/`` folder and offers a
single password field to set ``OPENAI_API_KEY``. Once set, we persist it to the
app instance's ``.env`` file (if available) and proceed to start streaming.
"""

import os
import sys
import time
import asyncio
import logging
from typing import List, Optional
from pathlib import Path
from collections import deque

import numpy as np
from fastrtc import AdditionalOutputs, audio_to_float32
from numpy.typing import NDArray
from scipy.signal import resample

from reachy_mini import ReachyMini
from reachy_mini.media.media_manager import MediaBackend
from hey_robo.config import LOCKED_PROFILE, config
from hey_robo.wake_word import (
    WakeDetection,
    WakeWordDetector,
    WakeDetectorStatus,
    build_wake_word_detector,
)
from hey_robo.openai_realtime import OpenaiRealtimeHandler
from hey_robo.headless_personality_ui import mount_personality_routes


try:
    # FastAPI is provided by the Reachy Mini Apps runtime
    from fastapi import FastAPI, Response
    from pydantic import BaseModel
    from fastapi.responses import FileResponse, JSONResponse
    from starlette.staticfiles import StaticFiles
except Exception:  # pragma: no cover - only loaded when settings_app is used
    FastAPI = object  # type: ignore
    FileResponse = object  # type: ignore
    JSONResponse = object  # type: ignore
    StaticFiles = object  # type: ignore
    BaseModel = object  # type: ignore


logger = logging.getLogger(__name__)


class LocalStream:
    """LocalStream using Reachy Mini's recorder/player."""

    def __init__(
        self,
        handler: OpenaiRealtimeHandler,
        robot: ReachyMini,
        *,
        settings_app: Optional[FastAPI] = None,
        instance_path: Optional[str] = None,
    ):
        """Initialize the stream with an OpenAI realtime handler and pipelines.

        - ``settings_app``: the Reachy Mini Apps FastAPI to attach settings endpoints.
        - ``instance_path``: directory where per-instance ``.env`` should be stored.
        """
        self.handler = handler
        self._robot = robot
        self._stop_event = asyncio.Event()
        self._tasks: List[asyncio.Task[None]] = []
        # Allow the handler to flush the player queue when appropriate.
        self.handler._clear_queue = self.clear_audio_queue
        self._settings_app: Optional[FastAPI] = settings_app
        self._instance_path: Optional[str] = instance_path
        self._settings_initialized = False
        self._asyncio_loop = None
        self._wake_detector: WakeWordDetector | None = None
        self._wake_status = WakeDetectorStatus(
            ready=False,
            engine=str(getattr(config, "WAKE_ENGINE", "vosk")),
            message="Wake detector has not been initialized.",
            model_path=str(getattr(config, "WAKE_MODEL_PATH", "") or "") or None,
        )
        self._realtime_active = False
        self._handler_task: asyncio.Task[None] | None = None
        self._pending_audio_frames: deque[tuple[int, NDArray[np.int16]]] = deque(maxlen=120)
        self._session_timeout_seconds = max(5.0, float(getattr(config, "WAKE_SESSION_TIMEOUT_SECONDS", 45.0)))

    # ---- Settings UI (only when API key is missing) ----
    def _read_env_lines(self, env_path: Path) -> list[str]:
        """Load env file contents or a template as a list of lines."""
        inst = env_path.parent
        try:
            if env_path.exists():
                try:
                    return env_path.read_text(encoding="utf-8").splitlines()
                except Exception:
                    return []
            template_text = None
            ex = inst / ".env.example"
            if ex.exists():
                try:
                    template_text = ex.read_text(encoding="utf-8")
                except Exception:
                    template_text = None
            if template_text is None:
                try:
                    cwd_example = Path.cwd() / ".env.example"
                    if cwd_example.exists():
                        template_text = cwd_example.read_text(encoding="utf-8")
                except Exception:
                    template_text = None
            if template_text is None:
                packaged = Path(__file__).parent / ".env.example"
                if packaged.exists():
                    try:
                        template_text = packaged.read_text(encoding="utf-8")
                    except Exception:
                        template_text = None
            return template_text.splitlines() if template_text else []
        except Exception:
            return []

    def _persist_api_key(self, key: str) -> None:
        """Persist API key to environment and instance ``.env`` if possible.

        Behavior:
        - Always sets ``OPENAI_API_KEY`` in process env and in-memory config.
        - Writes/updates ``<instance_path>/.env``:
          * If ``.env`` exists, replaces/append OPENAI_API_KEY line.
          * Else, copies template from ``<instance_path>/.env.example`` when present,
            otherwise falls back to the packaged template
            ``hey_robo/.env.example``.
          * Ensures the resulting file contains the full template plus the key.
        - Loads the written ``.env`` into the current process environment.
        """
        k = (key or "").strip()
        if not k:
            return
        # Update live process env and config so consumers see it immediately
        try:
            os.environ["OPENAI_API_KEY"] = k
        except Exception:  # best-effort
            pass
        try:
            config.OPENAI_API_KEY = k
        except Exception:
            pass

        if not self._instance_path:
            return
        try:
            inst = Path(self._instance_path)
            env_path = inst / ".env"
            lines = self._read_env_lines(env_path)
            replaced = False
            for i, ln in enumerate(lines):
                if ln.strip().startswith("OPENAI_API_KEY="):
                    lines[i] = f"OPENAI_API_KEY={k}"
                    replaced = True
                    break
            if not replaced:
                lines.append(f"OPENAI_API_KEY={k}")
            final_text = "\n".join(lines) + "\n"
            env_path.write_text(final_text, encoding="utf-8")
            logger.info("Persisted OPENAI_API_KEY to %s", env_path)

            # Load the newly written .env into this process to ensure downstream imports see it
            try:
                from dotenv import load_dotenv

                load_dotenv(dotenv_path=str(env_path), override=True)
            except Exception:
                pass
        except Exception as e:
            logger.warning("Failed to persist OPENAI_API_KEY: %s", e)

    def _persist_personality(self, profile: Optional[str]) -> None:
        """Persist the startup personality to the instance .env and config."""
        if LOCKED_PROFILE is not None:
            return
        selection = (profile or "").strip() or None
        try:
            from hey_robo.config import set_custom_profile

            set_custom_profile(selection)
        except Exception:
            pass

        if not self._instance_path:
            return
        try:
            env_path = Path(self._instance_path) / ".env"
            lines = self._read_env_lines(env_path)
            replaced = False
            for i, ln in enumerate(list(lines)):
                if ln.strip().startswith("REACHY_MINI_CUSTOM_PROFILE="):
                    if selection:
                        lines[i] = f"REACHY_MINI_CUSTOM_PROFILE={selection}"
                    else:
                        lines.pop(i)
                    replaced = True
                    break
            if selection and not replaced:
                lines.append(f"REACHY_MINI_CUSTOM_PROFILE={selection}")
            if selection is None and not env_path.exists():
                return
            final_text = "\n".join(lines) + "\n"
            env_path.write_text(final_text, encoding="utf-8")
            logger.info("Persisted startup personality to %s", env_path)
            try:
                from dotenv import load_dotenv

                load_dotenv(dotenv_path=str(env_path), override=True)
            except Exception:
                pass
        except Exception as e:
            logger.warning("Failed to persist REACHY_MINI_CUSTOM_PROFILE: %s", e)

    def _read_persisted_personality(self) -> Optional[str]:
        """Read persisted startup personality from instance .env (if any)."""
        if not self._instance_path:
            return None
        env_path = Path(self._instance_path) / ".env"
        try:
            if env_path.exists():
                for ln in env_path.read_text(encoding="utf-8").splitlines():
                    if ln.strip().startswith("REACHY_MINI_CUSTOM_PROFILE="):
                        _, _, val = ln.partition("=")
                        v = val.strip()
                        return v or None
        except Exception:
            pass
        return None

    def _wake_enabled(self) -> bool:
        """Return whether local wake phrase gating is enabled."""
        return bool(getattr(config, "WAKE_ENABLED", True))

    def _build_wake_detector(self) -> None:
        """Initialize the configured local wake phrase detector."""
        self._wake_detector = build_wake_word_detector(
            enabled=self._wake_enabled(),
            engine=str(getattr(config, "WAKE_ENGINE", "vosk")),
            wake_phrase=str(getattr(config, "WAKE_PHRASE", "HEY ROBO")),
            model_path=str(getattr(config, "WAKE_MODEL_PATH", "") or ""),
            instance_path=self._instance_path,
            sample_rate=int(getattr(config, "WAKE_SAMPLE_RATE", 16_000)),
            min_interval_seconds=float(getattr(config, "WAKE_ACTIVATION_MIN_INTERVAL_SECONDS", 2.0)),
        )
        self._wake_status = self._wake_detector.status
        if self._wake_status.ready:
            logger.info("Wake detector ready: %s", self._wake_status.message)
        else:
            logger.error("Wake detector unavailable: %s", self._wake_status.message)

    async def _activate_realtime(self, detection: WakeDetection) -> None:
        """Start the OpenAI Realtime session after a wake phrase detection."""
        if self._realtime_active:
            return

        logger.info(
            "Wake phrase detected via %s: phrase=%r transcript=%r confidence=%s",
            detection.source,
            detection.phrase,
            detection.transcript,
            detection.confidence,
        )
        self._realtime_active = True
        self._pending_audio_frames.clear()

        try:
            self.handler.idle_signals_enabled = False
            self.handler.last_activity_time = asyncio.get_running_loop().time()
        except Exception:
            pass

        self._handler_task = asyncio.create_task(self.handler.start_up(), name="openai-handler")
        self._tasks.append(self._handler_task)
        self._handler_task.add_done_callback(self._handle_realtime_done)

    def _handle_realtime_done(self, task: asyncio.Task[None]) -> None:
        """Mark wake mode active again when the Realtime task exits."""
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.error("Realtime session exited with an error: %s", exc)
        if self._realtime_active:
            logger.info("Realtime session ended; returning to wake mode.")
        self._realtime_active = False
        self._handler_task = None
        self._pending_audio_frames.clear()

    async def _deactivate_realtime(self, reason: str) -> None:
        """Close the active Realtime session and return to wake mode."""
        if not self._realtime_active and self._handler_task is None:
            return

        logger.info("Closing Realtime session: %s", reason)
        self._realtime_active = False
        self._pending_audio_frames.clear()
        self.clear_audio_queue()

        task = self._handler_task
        try:
            await self.handler.shutdown()
        except Exception as exc:
            logger.debug("handler.shutdown() failed during wake deactivation: %s", exc)

        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("Realtime task ended during shutdown: %s", exc)

        if task in self._tasks:
            self._tasks.remove(task)
        self._handler_task = None

    async def _send_or_buffer_active_frame(self, frame: tuple[int, NDArray[np.int16]]) -> None:
        """Send active-session audio, buffering briefly while Realtime connects."""
        if self.handler.connection is None:
            sample_rate, audio_frame = frame
            self._pending_audio_frames.append((sample_rate, np.copy(audio_frame)))
            return

        while self._pending_audio_frames:
            await self.handler.receive(self._pending_audio_frames.popleft())
        await self.handler.receive(frame)

    async def _session_watch_loop(self) -> None:
        """Return to wake mode after a Realtime session has been idle."""
        while not self._stop_event.is_set():
            await asyncio.sleep(1.0)
            if not self._wake_enabled() or not self._realtime_active:
                continue
            if self.handler.connection is None:
                continue

            try:
                idle_for = asyncio.get_running_loop().time() - float(self.handler.last_activity_time)
            except Exception:
                idle_for = 0.0
            if idle_for >= self._session_timeout_seconds:
                await self._deactivate_realtime(
                    f"no user or assistant activity for {self._session_timeout_seconds:.0f}s"
                )

    def _init_settings_ui_if_needed(self) -> None:
        """Attach minimal settings UI to the settings app.

        Always mounts the UI when a settings_app is provided so that users
        see a confirmation message even if the API key is already configured.
        """
        if self._settings_initialized:
            return
        if self._settings_app is None:
            return

        static_dir = Path(__file__).parent / "static"
        index_file = static_dir / "index.html"

        if hasattr(self._settings_app, "mount"):
            try:
                # Serve /static/* assets
                self._settings_app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
            except Exception:
                pass

        class ApiKeyPayload(BaseModel):
            openai_api_key: str

        class AppSettingsPayload(BaseModel):
            openai_api_key: str | None = None
            wake_enabled: bool | None = None
            wake_phrase: str | None = None
            wake_model_path: str | None = None
            wake_session_timeout_seconds: float | None = None
            realtime_voice: str | None = None
            codex_relay_url: str | None = None
            codex_relay_token: str | None = None
            codex_default_workspace: str | None = None

        def _persist_env_values(values: dict[str, str]) -> None:
            clean_values = {key: value.strip() for key, value in values.items() if value is not None}
            clean_values = {key: value for key, value in clean_values.items() if value}
            if not clean_values:
                return

            for key, value in clean_values.items():
                os.environ[key] = value

            config_map = {
                "OPENAI_API_KEY": "OPENAI_API_KEY",
                "HEY_ROBO_WAKE_ENABLED": "WAKE_ENABLED",
                "HEY_ROBO_WAKE_PHRASE": "WAKE_PHRASE",
                "HEY_ROBO_WAKE_MODEL_PATH": "WAKE_MODEL_PATH",
                "HEY_ROBO_WAKE_SESSION_TIMEOUT_SECONDS": "WAKE_SESSION_TIMEOUT_SECONDS",
                "HEY_ROBO_REALTIME_VOICE": "REALTIME_VOICE",
                "HEY_ROBO_CODEX_RELAY_URL": "CODEX_RELAY_URL",
                "HEY_ROBO_CODEX_RELAY_TOKEN": "CODEX_RELAY_TOKEN",
                "HEY_ROBO_CODEX_DEFAULT_WORKSPACE": "CODEX_DEFAULT_WORKSPACE",
            }
            for env_key, attr in config_map.items():
                if env_key in clean_values:
                    try:
                        value: str | bool | float = clean_values[env_key]
                        if env_key == "HEY_ROBO_WAKE_ENABLED":
                            value = value.strip().lower() in {"1", "true", "yes", "on"}
                        elif env_key == "HEY_ROBO_WAKE_SESSION_TIMEOUT_SECONDS":
                            value = max(5.0, float(value))
                            self._session_timeout_seconds = value
                        setattr(config, attr, value)
                    except Exception:
                        pass

            if not self._instance_path:
                return

            env_path = Path(self._instance_path) / ".env"
            lines = self._read_env_lines(env_path)
            for key, value in clean_values.items():
                replaced = False
                for i, line in enumerate(lines):
                    if line.strip().startswith(f"{key}="):
                        lines[i] = f"{key}={value}"
                        replaced = True
                        break
                if not replaced:
                    lines.append(f"{key}={value}")
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            logger.info("Persisted HeyRobo settings to %s", env_path)

            try:
                from dotenv import load_dotenv

                load_dotenv(dotenv_path=str(env_path), override=True)
            except Exception:
                pass

        def _settings_response() -> JSONResponse:
            has_key = bool(config.OPENAI_API_KEY and str(config.OPENAI_API_KEY).strip())
            has_relay_token = bool(getattr(config, "CODEX_RELAY_TOKEN", "") and str(config.CODEX_RELAY_TOKEN).strip())
            wake_status = self._wake_status
            return JSONResponse(
                {
                    "has_key": has_key,
                    "wake_enabled": bool(getattr(config, "WAKE_ENABLED", True)),
                    "wake_phrase": getattr(config, "WAKE_PHRASE", "HEY ROBO"),
                    "wake_engine": getattr(config, "WAKE_ENGINE", "vosk"),
                    "wake_model_path": getattr(config, "WAKE_MODEL_PATH", ""),
                    "wake_detector_ready": wake_status.ready,
                    "wake_status": wake_status.message,
                    "wake_session_timeout_seconds": self._session_timeout_seconds,
                    "realtime_voice": getattr(config, "REALTIME_VOICE", "cedar"),
                    "codex_relay_url": getattr(config, "CODEX_RELAY_URL", "http://127.0.0.1:8766"),
                    "has_codex_relay_token": has_relay_token,
                    "codex_default_workspace": getattr(config, "CODEX_DEFAULT_WORKSPACE", "current"),
                }
            )

        # GET / -> index.html
        @self._settings_app.get("/")
        def _root() -> FileResponse:
            return FileResponse(str(index_file))

        # GET /favicon.ico -> optional, avoid noisy 404s on some browsers
        @self._settings_app.get("/favicon.ico")
        def _favicon() -> Response:
            return Response(status_code=204)

        # GET /status -> whether key is set
        @self._settings_app.get("/status")
        def _status() -> JSONResponse:
            return _settings_response()

        @self._settings_app.get("/settings")
        def _settings() -> JSONResponse:
            return _settings_response()

        # GET /ready -> whether backend finished loading tools
        @self._settings_app.get("/ready")
        def _ready() -> JSONResponse:
            try:
                mod = sys.modules.get("hey_robo.tools.core_tools")
                ready = bool(getattr(mod, "_TOOLS_INITIALIZED", False)) if mod else False
            except Exception:
                ready = False
            return JSONResponse({"ready": ready})

        # POST /openai_api_key -> set/persist key
        @self._settings_app.post("/openai_api_key")
        def _set_key(payload: ApiKeyPayload) -> JSONResponse:
            key = (payload.openai_api_key or "").strip()
            if not key:
                return JSONResponse({"ok": False, "error": "empty_key"}, status_code=400)
            self._persist_api_key(key)
            return JSONResponse({"ok": True})

        @self._settings_app.post("/settings")
        def _save_settings(payload: AppSettingsPayload) -> JSONResponse:
            values: dict[str, str] = {}
            if payload.openai_api_key and payload.openai_api_key.strip():
                values["OPENAI_API_KEY"] = payload.openai_api_key
            if payload.wake_enabled is not None:
                values["HEY_ROBO_WAKE_ENABLED"] = "true" if payload.wake_enabled else "false"
            if payload.wake_phrase and payload.wake_phrase.strip():
                values["HEY_ROBO_WAKE_PHRASE"] = payload.wake_phrase
            if payload.wake_model_path and payload.wake_model_path.strip():
                values["HEY_ROBO_WAKE_MODEL_PATH"] = payload.wake_model_path
            if payload.wake_session_timeout_seconds is not None:
                values["HEY_ROBO_WAKE_SESSION_TIMEOUT_SECONDS"] = str(payload.wake_session_timeout_seconds)
            if payload.realtime_voice and payload.realtime_voice.strip():
                values["HEY_ROBO_REALTIME_VOICE"] = payload.realtime_voice
            if payload.codex_relay_url and payload.codex_relay_url.strip():
                values["HEY_ROBO_CODEX_RELAY_URL"] = payload.codex_relay_url
            if payload.codex_relay_token and payload.codex_relay_token.strip():
                values["HEY_ROBO_CODEX_RELAY_TOKEN"] = payload.codex_relay_token
            if payload.codex_default_workspace and payload.codex_default_workspace.strip():
                values["HEY_ROBO_CODEX_DEFAULT_WORKSPACE"] = payload.codex_default_workspace

            if not values:
                return JSONResponse({"ok": False, "error": "empty_settings"}, status_code=400)
            _persist_env_values(values)
            return JSONResponse({"ok": True})

        # POST /validate_api_key -> validate key without persisting it
        @self._settings_app.post("/validate_api_key")
        async def _validate_key(payload: ApiKeyPayload) -> JSONResponse:
            key = (payload.openai_api_key or "").strip()
            if not key:
                return JSONResponse({"valid": False, "error": "empty_key"}, status_code=400)

            # Try to validate by checking if we can fetch the models
            try:
                import httpx

                headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get("https://api.openai.com/v1/models", headers=headers)
                    if response.status_code == 200:
                        return JSONResponse({"valid": True})
                    elif response.status_code == 401:
                        return JSONResponse({"valid": False, "error": "invalid_api_key"}, status_code=401)
                    else:
                        return JSONResponse(
                            {"valid": False, "error": "validation_failed"}, status_code=response.status_code
                        )
            except Exception as e:
                logger.warning(f"API key validation failed: {e}")
                return JSONResponse({"valid": False, "error": "validation_error"}, status_code=500)

        self._settings_initialized = True

    def launch(self) -> None:
        """Start the recorder/player and run the async processing loops.

        If the OpenAI key is missing, expose a tiny settings UI via the
        Reachy Mini settings server to collect it before starting streams.
        """
        self._stop_event.clear()

        # Try to load an existing instance .env first (covers subsequent runs)
        if self._instance_path:
            try:
                from dotenv import load_dotenv

                from hey_robo.config import set_custom_profile

                env_path = Path(self._instance_path) / ".env"
                if env_path.exists():
                    load_dotenv(dotenv_path=str(env_path), override=True)
                    # Update config with newly loaded values
                    new_key = os.getenv("OPENAI_API_KEY", "").strip()
                    if new_key:
                        try:
                            config.OPENAI_API_KEY = new_key
                        except Exception:
                            pass
                    wake_phrase = os.getenv("HEY_ROBO_WAKE_PHRASE")
                    if wake_phrase:
                        try:
                            config.WAKE_PHRASE = wake_phrase.strip()
                        except Exception:
                            pass
                    wake_enabled = os.getenv("HEY_ROBO_WAKE_ENABLED")
                    if wake_enabled is not None:
                        try:
                            config.WAKE_ENABLED = wake_enabled.strip().lower() in {"1", "true", "yes", "on"}
                        except Exception:
                            pass
                    wake_model_path = os.getenv("HEY_ROBO_WAKE_MODEL_PATH")
                    if wake_model_path:
                        try:
                            config.WAKE_MODEL_PATH = wake_model_path.strip()
                        except Exception:
                            pass
                    wake_timeout = os.getenv("HEY_ROBO_WAKE_SESSION_TIMEOUT_SECONDS")
                    if wake_timeout:
                        try:
                            config.WAKE_SESSION_TIMEOUT_SECONDS = max(5.0, float(wake_timeout.strip()))
                            self._session_timeout_seconds = config.WAKE_SESSION_TIMEOUT_SECONDS
                        except Exception:
                            pass
                    realtime_voice = os.getenv("HEY_ROBO_REALTIME_VOICE") or os.getenv("OPENAI_REALTIME_VOICE")
                    if realtime_voice:
                        try:
                            config.REALTIME_VOICE = realtime_voice.strip()
                        except Exception:
                            pass
                    relay_url = os.getenv("HEY_ROBO_CODEX_RELAY_URL")
                    if relay_url:
                        try:
                            config.CODEX_RELAY_URL = relay_url.strip()
                        except Exception:
                            pass
                    relay_token = os.getenv("HEY_ROBO_CODEX_RELAY_TOKEN")
                    if relay_token:
                        try:
                            config.CODEX_RELAY_TOKEN = relay_token.strip()
                        except Exception:
                            pass
                    default_workspace = os.getenv("HEY_ROBO_CODEX_DEFAULT_WORKSPACE")
                    if default_workspace:
                        try:
                            config.CODEX_DEFAULT_WORKSPACE = default_workspace.strip()
                        except Exception:
                            pass
                    if LOCKED_PROFILE is None:
                        new_profile = os.getenv("REACHY_MINI_CUSTOM_PROFILE")
                        if new_profile is not None:
                            try:
                                set_custom_profile(new_profile.strip() or None)
                            except Exception:
                                pass  # Best-effort profile update
            except Exception:
                pass  # Instance .env loading is optional; continue with defaults

        # If key is still missing, try to download one from HuggingFace
        if not (config.OPENAI_API_KEY and str(config.OPENAI_API_KEY).strip()):
            logger.info("OPENAI_API_KEY not set, attempting to download from HuggingFace...")
            try:
                from gradio_client import Client
                client = Client("HuggingFaceM4/gradium_setup", verbose=False)
                key, status = client.predict(api_name="/claim_b_key")
                if key and key.strip():
                    logger.info("Successfully downloaded API key from HuggingFace")
                    # Persist it immediately
                    self._persist_api_key(key)
            except Exception as e:
                logger.warning(f"Failed to download API key from HuggingFace: {e}")

        # Always expose settings UI if a settings app is available
        # (do this AFTER loading/downloading the key so status endpoint sees the right value)
        self._init_settings_ui_if_needed()

        # If key is still missing -> wait until provided via the settings UI
        if not (config.OPENAI_API_KEY and str(config.OPENAI_API_KEY).strip()):
            logger.warning("OPENAI_API_KEY not found. Open the app settings page to enter it.")
            # Poll until the key becomes available (set via the settings UI)
            try:
                while not (config.OPENAI_API_KEY and str(config.OPENAI_API_KEY).strip()):
                    time.sleep(0.2)
            except KeyboardInterrupt:
                logger.info("Interrupted while waiting for API key.")
                return

        # Start media after key is set/available
        self._robot.media.start_recording()
        self._robot.media.start_playing()
        time.sleep(1)  # give some time to the pipelines to start

        async def runner() -> None:
            # Capture loop for cross-thread personality actions
            loop = asyncio.get_running_loop()
            self._asyncio_loop = loop  # type: ignore[assignment]
            # Mount personality routes now that loop and handler are available
            try:
                if self._settings_app is not None:
                    mount_personality_routes(
                        self._settings_app,
                        self.handler,
                        lambda: self._asyncio_loop,
                        persist_personality=self._persist_personality,
                        get_persisted_personality=self._read_persisted_personality,
                    )
            except Exception:
                pass
            self._tasks = [
                asyncio.create_task(self.record_loop(), name="stream-record-loop"),
                asyncio.create_task(self.play_loop(), name="stream-play-loop"),
            ]
            if self._wake_enabled():
                self._build_wake_detector()
                self.handler.idle_signals_enabled = False
                self._tasks.append(asyncio.create_task(self._session_watch_loop(), name="wake-session-watch"))
            else:
                logger.info("Wake detection disabled; starting Realtime immediately.")
                self._realtime_active = True
                self.handler.idle_signals_enabled = True
                self._handler_task = asyncio.create_task(self.handler.start_up(), name="openai-handler")
                self._tasks.append(self._handler_task)
            try:
                await asyncio.gather(*self._tasks)
            except asyncio.CancelledError:
                logger.info("Tasks cancelled during shutdown")
            finally:
                # Ensure handler connection is closed
                await self._deactivate_realtime("stream shutdown")

        asyncio.run(runner())

    def close(self) -> None:
        """Stop the stream and underlying media pipelines.

        This method:
        - Stops audio recording and playback first
        - Sets the stop event to signal async loops to terminate
        - Cancels all pending async tasks (openai-handler, record-loop, play-loop)
        """
        logger.info("Stopping LocalStream...")

        # Stop media pipelines FIRST before cancelling async tasks
        # This ensures clean shutdown before PortAudio cleanup
        try:
            self._robot.media.stop_recording()
        except Exception as e:
            logger.debug(f"Error stopping recording (may already be stopped): {e}")

        try:
            self._robot.media.stop_playing()
        except Exception as e:
            logger.debug(f"Error stopping playback (may already be stopped): {e}")

        # Now signal async loops to stop
        self._stop_event.set()

        # Cancel all running tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

    def clear_audio_queue(self) -> None:
        """Flush the player's appsrc to drop any queued audio immediately."""
        logger.info("User intervention: flushing player queue")
        backend = getattr(self._robot.media, "backend", None)
        if backend == MediaBackend.GSTREAMER:
            # Directly flush gstreamer audio pipe
            self._robot.media.audio.clear_player()
        elif backend == MediaBackend.DEFAULT or backend == MediaBackend.DEFAULT_NO_VIDEO:
            self._robot.media.audio.clear_output_buffer()
        self.handler.output_queue = asyncio.Queue()

    async def record_loop(self) -> None:
        """Read mic frames from the recorder and forward them to the handler."""
        input_sample_rate = self._robot.media.get_input_audio_samplerate()
        logger.debug(f"Audio recording started at {input_sample_rate} Hz")

        while not self._stop_event.is_set():
            audio_frame = self._robot.media.get_audio_sample()
            if audio_frame is None:
                await asyncio.sleep(0.01)
                continue

            frame = (input_sample_rate, audio_frame)
            if self._wake_enabled():
                if self._realtime_active:
                    await self._send_or_buffer_active_frame(frame)
                elif self._wake_detector and self._wake_detector.status.ready:
                    detection = self._wake_detector.accept_frame(frame)
                    if detection is not None:
                        await self._activate_realtime(detection)
                else:
                    await asyncio.sleep(0.02)
            else:
                await self.handler.receive(frame)
            await asyncio.sleep(0)  # avoid busy loop

    async def play_loop(self) -> None:
        """Fetch outputs from the handler: log text and play audio frames."""
        while not self._stop_event.is_set():
            if self._wake_enabled() and not self._realtime_active:
                await asyncio.sleep(0.05)
                continue

            try:
                handler_output = await asyncio.wait_for(self.handler.emit(), timeout=0.25)
            except asyncio.TimeoutError:
                continue

            if isinstance(handler_output, AdditionalOutputs):
                for msg in handler_output.args:
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        logger.info(
                            "role=%s content=%s",
                            msg.get("role"),
                            content if len(content) < 500 else content[:500] + "…",
                        )

            elif isinstance(handler_output, tuple):
                input_sample_rate, audio_data = handler_output
                output_sample_rate = self._robot.media.get_output_audio_samplerate()

                # Reshape if needed
                if audio_data.ndim == 2:
                    # Scipy channels last convention
                    if audio_data.shape[1] > audio_data.shape[0]:
                        audio_data = audio_data.T
                    # Multiple channels -> Mono channel
                    if audio_data.shape[1] > 1:
                        audio_data = audio_data[:, 0]

                # Cast if needed
                audio_frame = audio_to_float32(audio_data)

                # Resample if needed
                if input_sample_rate != output_sample_rate:
                    audio_frame = resample(
                        audio_frame,
                        int(len(audio_frame) * output_sample_rate / input_sample_rate),
                    )

                self._robot.media.push_audio_sample(audio_frame)

            else:
                logger.debug("Ignoring output type=%s", type(handler_output).__name__)

            await asyncio.sleep(0)  # yield to event loop
