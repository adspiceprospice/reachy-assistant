"""Tests for local wake phrase detection helpers."""

from pathlib import Path

import numpy as np

from hey_robo.wake_word import (
    WakeDetection,
    phrase_matches,
    normalize_phrase,
    build_wake_word_detector,
)


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
