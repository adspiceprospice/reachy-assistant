"""Offline wake phrase detection for HeyRobo.

The detector is intentionally optional at import time: the app can be installed
without Vosk, but wake detection reports a clear unavailable status until the
runtime dependency and model path are configured.
"""

from __future__ import annotations
import json
import time
import logging
from typing import Any, Protocol
from difflib import SequenceMatcher
from pathlib import Path
from dataclasses import dataclass

import numpy as np
from fastrtc import audio_to_int16
from numpy.typing import NDArray
from scipy.signal import resample


logger = logging.getLogger(__name__)


def normalize_phrase(value: str) -> str:
    """Return lowercase alphanumeric words separated by single spaces."""
    chars: list[str] = []
    last_was_space = True
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
            last_was_space = False
        elif not last_was_space:
            chars.append(" ")
            last_was_space = True
    return "".join(chars).strip()


def phrase_matches(transcript: str, wake_phrase: str, *, fuzzy_threshold: float = 0.86) -> bool:
    """Return whether a local transcript appears to contain the wake phrase."""
    phrase = normalize_phrase(wake_phrase)
    text = normalize_phrase(transcript)
    if not phrase or not text:
        return False
    if phrase in text:
        return True

    phrase_words = phrase.split()
    text_words = text.split()
    if len(text_words) < len(phrase_words):
        return SequenceMatcher(None, text, phrase).ratio() >= fuzzy_threshold

    window_size = len(phrase_words)
    for index in range(0, len(text_words) - window_size + 1):
        candidate = " ".join(text_words[index : index + window_size])
        if SequenceMatcher(None, candidate, phrase).ratio() >= fuzzy_threshold:
            return True
    return False


@dataclass(frozen=True)
class WakeDetection:
    """A positive wake phrase detection event."""

    phrase: str
    transcript: str
    confidence: float | None = None
    source: str = "vosk"


@dataclass(frozen=True)
class WakeDetectorStatus:
    """Runtime status for the wake phrase detector."""

    ready: bool
    engine: str
    message: str
    model_path: str | None = None


class WakeWordDetector(Protocol):
    """Protocol implemented by wake phrase detectors."""

    @property
    def status(self) -> WakeDetectorStatus:
        """Return detector readiness and diagnostics."""
        ...

    def accept_frame(self, frame: tuple[int, NDArray[np.int16]]) -> WakeDetection | None:
        """Process one microphone frame and return a wake event when detected."""
        ...


class UnavailableWakeWordDetector:
    """Wake detector that reports a configuration/runtime problem."""

    def __init__(self, *, engine: str, message: str, model_path: str | None = None) -> None:
        """Initialize an unavailable detector with a user-facing message."""
        self._status = WakeDetectorStatus(
            ready=False,
            engine=engine,
            message=message,
            model_path=model_path,
        )

    @property
    def status(self) -> WakeDetectorStatus:
        """Return detector readiness and diagnostics."""
        return self._status

    def accept_frame(self, frame: tuple[int, NDArray[np.int16]]) -> WakeDetection | None:
        """Ignore audio because the detector is unavailable."""
        return None


class VoskWakeWordDetector:
    """Vosk-backed local wake phrase detector."""

    def __init__(
        self,
        *,
        wake_phrase: str,
        model_path: Path,
        sample_rate: int = 16_000,
        min_interval_seconds: float = 2.0,
        min_confidence: float = 0.70,
        vosk_module: Any | None = None,
    ) -> None:
        """Create a detector for a wake phrase and Vosk model directory."""
        phrase = normalize_phrase(wake_phrase)
        if not phrase:
            raise ValueError("wake_phrase must not be empty")
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if not model_path.is_dir():
            raise FileNotFoundError(f"Vosk model directory not found: {model_path}")

        if vosk_module is None:
            try:
                import vosk as vosk_module  # type: ignore[no-redef]
            except Exception as exc:  # pragma: no cover - depends on optional runtime dependency
                raise RuntimeError("The 'vosk' package is required for wake detection") from exc

        self.wake_phrase = phrase
        self.model_path = model_path
        self.sample_rate = sample_rate
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.min_confidence = max(0.0, min_confidence)
        self._vosk = vosk_module
        self._model = self._vosk.Model(str(model_path))
        self._recognizer = self._create_recognizer()
        self._last_detection_time = 0.0
        self._status = WakeDetectorStatus(
            ready=True,
            engine="vosk",
            message=f"Listening for '{wake_phrase.strip()}'.",
            model_path=str(model_path),
        )

    @property
    def status(self) -> WakeDetectorStatus:
        """Return detector readiness and diagnostics."""
        return self._status

    def _create_recognizer(self) -> Any:
        """Create a recognizer constrained to the wake phrase grammar."""
        grammar = json.dumps([self.wake_phrase, "[unk]"])
        recognizer = self._vosk.KaldiRecognizer(self._model, self.sample_rate, grammar)
        try:
            recognizer.SetWords(True)
        except Exception:
            pass
        return recognizer

    def _cooldown_active(self) -> bool:
        """Return whether a recent detection should suppress repeats."""
        return (time.monotonic() - self._last_detection_time) < self.min_interval_seconds

    def _mark_detected(self) -> None:
        """Record a detection and reset recognizer state."""
        self._last_detection_time = time.monotonic()
        self._recognizer = self._create_recognizer()

    def _parse_result(self, raw: str) -> tuple[str, float | None]:
        """Extract transcript and confidence from a Vosk JSON result."""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return "", None

        transcript = str(payload.get("text") or payload.get("partial") or "").strip()
        confidence: float | None = None
        results = payload.get("result")
        if isinstance(results, list) and results:
            values = [item.get("conf") for item in results if isinstance(item, dict)]
            numeric = [float(value) for value in values if isinstance(value, (int, float))]
            if numeric:
                confidence = sum(numeric) / len(numeric)
        return transcript, confidence

    def _process_result(self, raw: str) -> WakeDetection | None:
        """Return a wake detection when a recognizer result matches."""
        transcript, confidence = self._parse_result(raw)
        if not transcript:
            return None
        if phrase_matches(transcript, self.wake_phrase):
            if confidence is not None and confidence < self.min_confidence:
                logger.info(
                    "Wake phrase candidate ignored: transcript=%r confidence=%.3f threshold=%.3f",
                    transcript,
                    confidence,
                    self.min_confidence,
                )
                return None
            if confidence is None:
                logger.info(
                    "Wake phrase candidate accepted: transcript=%r confidence=unknown threshold=%.3f",
                    transcript,
                    self.min_confidence,
                )
            else:
                logger.info(
                    "Wake phrase candidate accepted: transcript=%r confidence=%.3f threshold=%.3f",
                    transcript,
                    confidence,
                    self.min_confidence,
                )
            self._mark_detected()
            return WakeDetection(
                phrase=self.wake_phrase,
                transcript=transcript,
                confidence=confidence,
                source="vosk",
            )
        return None

    def accept_frame(self, frame: tuple[int, NDArray[np.int16]]) -> WakeDetection | None:
        """Process one microphone frame and return a wake event when detected."""
        if self._cooldown_active():
            return None

        audio_bytes = _frame_to_pcm16_bytes(frame, target_sample_rate=self.sample_rate)
        if not audio_bytes:
            return None

        if self._recognizer.AcceptWaveform(audio_bytes):
            return self._process_result(self._recognizer.Result())
        return None


def _frame_to_pcm16_bytes(
    frame: tuple[int, NDArray[np.int16]],
    *,
    target_sample_rate: int,
) -> bytes:
    """Convert a microphone frame to mono PCM16 bytes for Vosk."""
    input_sample_rate, audio_frame = frame
    audio = audio_frame

    if audio.ndim == 2:
        if audio.shape[1] > audio.shape[0]:
            audio = audio.T
        if audio.shape[1] > 1:
            audio = audio[:, 0]
        else:
            audio = audio.reshape(-1)

    if audio.ndim != 1:
        audio = audio.reshape(-1)

    if input_sample_rate != target_sample_rate and len(audio) > 0:
        target_length = int(len(audio) * target_sample_rate / input_sample_rate)
        if target_length <= 0:
            return b""
        audio = resample(audio, target_length)

    return audio_to_int16(audio).tobytes()


def resolve_wake_model_path(configured_path: str | None, *, instance_path: str | None = None) -> Path | None:
    """Resolve the first existing Vosk model path from settings and common app locations."""
    candidates: list[Path] = []
    if configured_path and configured_path.strip():
        candidates.append(Path(configured_path).expanduser())

    if instance_path:
        root = Path(instance_path).expanduser()
        candidates.extend(
            [
                root / "wake_models" / "vosk-model-small-en-us-0.15",
                root / "vosk-model-small-en-us-0.15",
            ]
        )

    cwd = Path.cwd()
    candidates.extend(
        [
            cwd / "models" / "vosk-model-small-en-us-0.15",
            cwd / "wake_models" / "vosk-model-small-en-us-0.15",
        ]
    )

    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return candidates[0].resolve() if candidates else None


def build_wake_word_detector(
    *,
    enabled: bool,
    engine: str,
    wake_phrase: str,
    model_path: str | None,
    instance_path: str | None,
    sample_rate: int,
    min_interval_seconds: float,
    min_confidence: float = 0.70,
) -> WakeWordDetector:
    """Build the configured wake word detector or a diagnostic placeholder."""
    normalized_engine = (engine or "vosk").strip().lower()
    if not enabled:
        return UnavailableWakeWordDetector(
            engine=normalized_engine,
            message="Wake detection is disabled.",
            model_path=model_path,
        )
    if normalized_engine != "vosk":
        return UnavailableWakeWordDetector(
            engine=normalized_engine,
            message=f"Unsupported wake detector engine: {engine}",
            model_path=model_path,
        )

    resolved_path = resolve_wake_model_path(model_path, instance_path=instance_path)
    if resolved_path is None or not resolved_path.is_dir():
        return UnavailableWakeWordDetector(
            engine="vosk",
            message=(
                "Vosk wake model is not configured. Set HEY_ROBO_WAKE_MODEL_PATH "
                "to a local Vosk model directory."
            ),
            model_path=str(resolved_path) if resolved_path else model_path,
        )

    try:
        return VoskWakeWordDetector(
            wake_phrase=wake_phrase,
            model_path=resolved_path,
            sample_rate=sample_rate,
            min_interval_seconds=min_interval_seconds,
            min_confidence=min_confidence,
        )
    except Exception as exc:
        return UnavailableWakeWordDetector(
            engine="vosk",
            message=str(exc),
            model_path=str(resolved_path),
        )
