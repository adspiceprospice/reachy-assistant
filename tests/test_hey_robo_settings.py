from hey_robo.config import (
    config,
    parse_realtime_languages,
    realtime_languages_to_env,
    language_code_for_realtime_transcription,
)
from hey_robo.prompts import get_session_voice, get_session_instructions


def test_session_voice_uses_app_setting_when_profile_has_no_voice(monkeypatch):
    """Use the app voice setting when the locked profile has no voice file."""
    monkeypatch.setattr(config, "REALTIME_VOICE", "verse", raising=False)
    assert get_session_voice() == "verse"


def test_realtime_languages_are_parsed_and_deduplicated() -> None:
    """Parse a user-facing ordered language list for storage and prompts."""
    languages = parse_realtime_languages("Dutch, English; Nederlands, Spanish")

    assert languages == ["Dutch", "English", "Spanish"]
    assert realtime_languages_to_env(languages) == "Dutch, English, Spanish"


def test_language_label_maps_to_transcription_code() -> None:
    """Map common labels and regional codes to transcription hints."""
    assert language_code_for_realtime_transcription("Dutch") == "nl"
    assert language_code_for_realtime_transcription("Nederlands") == "nl"
    assert language_code_for_realtime_transcription("en-US") == "en"
    assert language_code_for_realtime_transcription("Klingon") is None


def test_session_instructions_include_ordered_language_preferences(monkeypatch) -> None:
    """Inject app language settings into the Realtime session prompt."""
    monkeypatch.setattr(config, "REALTIME_LANGUAGES", ["Dutch", "English"], raising=False)

    instructions = get_session_instructions()

    assert "Runtime language preference:" in instructions
    assert "1. Dutch, 2. English" in instructions
    assert "Treat Dutch as the default" in instructions
