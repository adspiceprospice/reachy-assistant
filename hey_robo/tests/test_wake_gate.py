"""Tests for LocalStream wake phrase gating."""

from __future__ import annotations
import asyncio
from dataclasses import dataclass

import numpy as np
import pytest

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
