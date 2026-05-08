"""Tests for local wake phrase detection helpers."""

from pathlib import Path

import numpy as np

from hey_robo.wake_word import (
    WakeDetection,
    VoskWakeWordDetector,
    phrase_matches,
    normalize_phrase,
    build_wake_word_detector,
)


class _FakeRecognizer:
    """Small Vosk recognizer fake for wake matching tests."""

    accept_waveform = True
    result_json = "{}"
    partial_json = "{}"

    def __init__(self, *_args) -> None:
        """Initialize fake recognizer."""
        self.words_enabled = False

    def SetWords(self, enabled: bool) -> None:
        """Record that word confidence was requested."""
        self.words_enabled = enabled

    def AcceptWaveform(self, _audio: bytes) -> bool:
        """Return the configured waveform acceptance value."""
        return self.accept_waveform

    def Result(self) -> str:
        """Return a configured final result."""
        return self.result_json

    def PartialResult(self) -> str:
        """Return a configured partial result."""
        return self.partial_json


class _FakeVosk:
    """Minimal Vosk module fake."""

    recognizer_cls = _FakeRecognizer

    class Model:
        def __init__(self, _path: str) -> None:
            """Accept any model path."""

    @classmethod
    def KaldiRecognizer(cls, *_args):
        """Return the configured recognizer fake."""
        return cls.recognizer_cls(*_args)


def test_normalize_phrase_keeps_words_only() -> None:
    """Normalize wake phrases for stable local matching."""
    assert normalize_phrase("  HEY, ROBO!! ") == "hey robo"


def test_phrase_matches_case_spacing_and_near_miss() -> None:
    """Match phrase variants that local speech recognition commonly returns."""
    assert phrase_matches("hey robo", "HEY ROBO")
    assert phrase_matches("okay hey robo can you help", "HEY ROBO")
    assert phrase_matches("hey robot", "HEY ROBO")
    assert not phrase_matches("robot turn left", "HEY ROBO")


def test_build_wake_detector_reports_missing_vosk_model(tmp_path: Path) -> None:
    """Missing model paths should produce a clear unavailable detector."""
    missing_path = tmp_path / "missing-model"
    detector = build_wake_word_detector(
        enabled=True,
        engine="vosk",
        wake_phrase="HEY ROBO",
        model_path=str(missing_path),
        instance_path=None,
        sample_rate=16_000,
        min_interval_seconds=2.0,
    )

    assert not detector.status.ready
    assert detector.status.engine == "vosk"
    assert "Vosk wake model" in detector.status.message
    assert detector.accept_frame((16_000, np.zeros(160, dtype=np.int16))) is None


def test_wake_detection_dataclass_defaults() -> None:
    """Wake detection events include the phrase and detector source."""
    detection = WakeDetection(phrase="hey robo", transcript="hey robo")

    assert detection.phrase == "hey robo"
    assert detection.transcript == "hey robo"
    assert detection.source == "vosk"


def test_vosk_detector_ignores_partial_wake_phrase(tmp_path: Path) -> None:
    """Partial Vosk guesses should not wake the robot by themselves."""

    class PartialRecognizer(_FakeRecognizer):
        accept_waveform = False
        partial_json = '{"partial":"hey robo"}'

    class PartialVosk(_FakeVosk):
        recognizer_cls = PartialRecognizer

    detector = VoskWakeWordDetector(
        wake_phrase="HEY ROBO",
        model_path=tmp_path,
        sample_rate=16_000,
        vosk_module=PartialVosk,
    )

    assert detector.accept_frame((16_000, np.zeros(160, dtype=np.int16))) is None


def test_vosk_detector_requires_minimum_confidence(tmp_path: Path) -> None:
    """Low-confidence final wake matches should be ignored."""

    class LowConfidenceRecognizer(_FakeRecognizer):
        result_json = '{"text":"hey robo","result":[{"conf":0.25},{"conf":0.35}]}'

    class LowConfidenceVosk(_FakeVosk):
        recognizer_cls = LowConfidenceRecognizer

    detector = VoskWakeWordDetector(
        wake_phrase="HEY ROBO",
        model_path=tmp_path,
        sample_rate=16_000,
        min_confidence=0.60,
        vosk_module=LowConfidenceVosk,
    )

    assert detector.accept_frame((16_000, np.zeros(160, dtype=np.int16))) is None
