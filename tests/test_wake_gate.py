"""Tests for LocalStream wake phrase gating."""

from __future__ import annotations
import sys
import types
import asyncio
import importlib.util
from dataclasses import dataclass

import numpy as np
import pytest


if importlib.util.find_spec("fastrtc") is None:
    fastrtc_stub = types.ModuleType("fastrtc")

    class _AdditionalOutputs:
        def __init__(self, *args) -> None:
            self.args = args

    class _AsyncStreamHandler:
        def __init__(self, **kwargs) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    fastrtc_stub.AdditionalOutputs = _AdditionalOutputs
    fastrtc_stub.AsyncStreamHandler = _AsyncStreamHandler
    fastrtc_stub.audio_to_int16 = lambda value: value
    fastrtc_stub.audio_to_float32 = lambda value: value

    async def _wait_for_item(queue):
        return await queue.get()

    fastrtc_stub.wait_for_item = _wait_for_item
    sys.modules["fastrtc"] = fastrtc_stub

if importlib.util.find_spec("cv2") is None:
    cv2_stub = types.ModuleType("cv2")
    cv2_stub.COLOR_BGR2RGB = 0
    cv2_stub.cvtColor = lambda image, _code: image
    sys.modules["cv2"] = cv2_stub

if importlib.util.find_spec("gradio") is None:
    gradio_stub = types.ModuleType("gradio")

    class _Image:
        def __init__(self, value=None) -> None:
            self.value = value

    gradio_stub.Image = _Image
    sys.modules["gradio"] = gradio_stub

if importlib.util.find_spec("openai") is None:
    openai_stub = types.ModuleType("openai")
    openai_stub.AsyncOpenAI = object
    sys.modules["openai"] = openai_stub

if importlib.util.find_spec("websockets") is None:
    websockets_stub = types.ModuleType("websockets")
    websocket_exceptions_stub = types.ModuleType("websockets.exceptions")

    class _ConnectionClosedError(Exception):
        pass

    websocket_exceptions_stub.ConnectionClosedError = _ConnectionClosedError
    sys.modules["websockets"] = websockets_stub
    sys.modules["websockets.exceptions"] = websocket_exceptions_stub

if importlib.util.find_spec("reachy_mini") is None:
    reachy_mini_stub = types.ModuleType("reachy_mini")
    reachy_mini_stub.ReachyMini = object
    media_module = types.ModuleType("reachy_mini.media")
    media_manager_module = types.ModuleType("reachy_mini.media.media_manager")

    class _MediaBackend:
        GSTREAMER = "gstreamer"
        DEFAULT = "default"
        DEFAULT_NO_VIDEO = "default_no_video"

    media_manager_module.MediaBackend = _MediaBackend
    sys.modules["reachy_mini"] = reachy_mini_stub
    sys.modules["reachy_mini.media"] = media_module
    sys.modules["reachy_mini.media.media_manager"] = media_manager_module

from hey_robo.config import config
from hey_robo.console import LocalStream
from hey_robo.wake_word import WakeDetection, WakeDetectorStatus


@dataclass
class _FakeDetector:
    """Wake detector that fires on a configured frame count."""

    fire_on: int

    def __post_init__(self) -> None:
        """Initialize call tracking."""
        self.calls = 0
        self.status = WakeDetectorStatus(
            ready=True,
            engine="test",
            message="ready",
            model_path=None,
        )

    def accept_frame(self, frame: tuple[int, np.ndarray]) -> WakeDetection | None:
        """Return a detection after enough frames have passed."""
        self.calls += 1
        if self.calls == self.fire_on:
            return WakeDetection(phrase="hey robo", transcript="hey robo", source="test")
        return None


class _FakeMedia:
    """Minimal media object for LocalStream record-loop tests."""

    def __init__(self, frames: list[np.ndarray]) -> None:
        """Initialize fake microphone frames."""
        self.frames = frames

    def get_input_audio_samplerate(self) -> int:
        """Return the fake microphone sample rate."""
        return 16_000

    def get_audio_sample(self) -> np.ndarray | None:
        """Return frames in order and then no audio."""
        if self.frames:
            return self.frames.pop(0)
        return None


class _FakeRobot:
    """Minimal robot object with media only."""

    def __init__(self, frames: list[np.ndarray]) -> None:
        """Initialize fake robot media."""
        self.media = _FakeMedia(frames)


class _FakeHandler:
    """Minimal Realtime handler used by LocalStream."""

    def __init__(self) -> None:
        """Initialize call tracking."""
        self.connection = None
        self.received: list[tuple[int, np.ndarray]] = []
        self.output_queue: asyncio.Queue[object] = asyncio.Queue()
        self.idle_signals_enabled = True
        self.last_activity_time = 0.0
        self.start_count = 0
        self._running = asyncio.Event()

    async def start_up(self) -> None:
        """Pretend to establish a Realtime session."""
        self.start_count += 1
        self.connection = object()
        await self._running.wait()

    async def receive(self, frame: tuple[int, np.ndarray]) -> None:
        """Record frames that would be forwarded to OpenAI."""
        self.received.append(frame)

    async def shutdown(self) -> None:
        """Stop the fake session."""
        self.connection = None
        self._running.set()


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    """Wait for an async test condition."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


@pytest.mark.asyncio
async def test_record_loop_starts_realtime_only_after_wake(monkeypatch) -> None:
    """Audio is gated locally until the wake detector fires."""
    monkeypatch.setattr(config, "WAKE_ENABLED", True, raising=False)
    frames = [np.zeros(160, dtype=np.int16) for _ in range(8)]
    handler = _FakeHandler()
    stream = LocalStream(handler, _FakeRobot(frames))
    detector = _FakeDetector(fire_on=3)
    stream._wake_detector = detector

    task = asyncio.create_task(stream.record_loop())
    try:
        await _wait_until(lambda: handler.start_count == 1)
        await _wait_until(lambda: len(handler.received) > 0)
    finally:
        stream._stop_event.set()
        await asyncio.wait_for(task, timeout=1.0)
        await stream._deactivate_realtime("test cleanup")

    assert detector.calls == 3
    assert handler.start_count == 1
    assert handler.received


@pytest.mark.asyncio
async def test_wake_state_transitions_queue_visible_pose_indicators(monkeypatch) -> None:
    """Realtime activation and standby return should queue distinct robot pose cues."""
    monkeypatch.setattr(config, "WAKE_ENABLED", True, raising=False)
    handler = _FakeHandler()
    stream = LocalStream(handler, _FakeRobot([]))
    queued_states: list[str] = []
    monkeypatch.setattr(stream, "_queue_realtime_state_pose", lambda state: queued_states.append(state))

    await stream._activate_realtime(WakeDetection(phrase="hey robo", transcript="hey robo", source="test"))
    await _wait_until(lambda: handler.start_count == 1)
    await stream._deactivate_realtime("test standby")

    assert queued_states == ["active", "standby"]
