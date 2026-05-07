from hey_robo.config import config
from hey_robo.prompts import get_session_voice


def test_session_voice_uses_app_setting_when_profile_has_no_voice(monkeypatch):
    """Use the app voice setting when the locked profile has no voice file."""
    monkeypatch.setattr(config, "REALTIME_VOICE", "verse", raising=False)
    assert get_session_voice() == "verse"
