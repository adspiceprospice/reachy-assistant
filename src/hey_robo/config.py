import os
import sys
import logging
import unicodedata
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


DEFAULT_REALTIME_LANGUAGES = ("English",)

_LANGUAGE_CODE_ALIASES = {
    "afrikaans": "af",
    "arabic": "ar",
    "ar": "ar",
    "basque": "eu",
    "catalan": "ca",
    "chinese": "zh",
    "zh": "zh",
    "czech": "cs",
    "danish": "da",
    "dutch": "nl",
    "nederlands": "nl",
    "nl": "nl",
    "english": "en",
    "en": "en",
    "finnish": "fi",
    "french": "fr",
    "francais": "fr",
    "fr": "fr",
    "german": "de",
    "deutsch": "de",
    "de": "de",
    "greek": "el",
    "hebrew": "he",
    "hindi": "hi",
    "hungarian": "hu",
    "indonesian": "id",
    "italian": "it",
    "italiano": "it",
    "it": "it",
    "japanese": "ja",
    "korean": "ko",
    "norwegian": "no",
    "polish": "pl",
    "portuguese": "pt",
    "portugues": "pt",
    "pt": "pt",
    "romanian": "ro",
    "russian": "ru",
    "spanish": "es",
    "espanol": "es",
    "es": "es",
    "swedish": "sv",
    "thai": "th",
    "turkish": "tr",
    "ukrainian": "uk",
    "vietnamese": "vi",
}


# Locked profile: set to a profile name (e.g., "astronomer") to lock the app
# to that profile and disable all profile switching. Leave as None for normal behavior.
LOCKED_PROFILE: str | None = "_hey_robo_locked_profile"
DEFAULT_PROFILES_DIRECTORY = Path(__file__).parent / "profiles"

logger = logging.getLogger(__name__)


def _fold_language_label(value: str) -> str:
    """Normalize a language label for comparison without changing display text."""
    return (
        unicodedata.normalize("NFKD", value.strip().strip('"').strip("'"))
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
    )


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean environment flag.

    Accepted truthy values: 1, true, yes, on
    Accepted falsy values: 0, false, no, off
    """
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False

    logger.warning("Invalid boolean value for %s=%r, using default=%s", name, raw, default)
    return default


def _env_float(name: str, default: float) -> float:
    """Parse a float environment value."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        logger.warning("Invalid float value for %s=%r, using default=%s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    """Parse an integer environment value."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("Invalid integer value for %s=%r, using default=%s", name, raw, default)
        return default


def parse_realtime_languages(value: object | None) -> list[str]:
    """Parse the ordered list of languages expected during Realtime sessions."""
    if value is None:
        return list(DEFAULT_REALTIME_LANGUAGES)

    if isinstance(value, (list, tuple)):
        parts = [str(item).strip() for item in value]
    else:
        raw = str(value).replace("\n", ",").replace(";", ",")
        parts = [part.strip() for part in raw.split(",")]

    languages: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = part.strip().strip('"').strip("'")
        if not cleaned:
            continue
        folded = _fold_language_label(cleaned).replace("_", "-")
        base, _, _region = folded.partition("-")
        key = _LANGUAGE_CODE_ALIASES.get(folded) or _LANGUAGE_CODE_ALIASES.get(base) or folded
        if key in seen:
            continue
        seen.add(key)
        languages.append(cleaned)
        if len(languages) >= 8:
            break

    return languages or list(DEFAULT_REALTIME_LANGUAGES)


def realtime_languages_to_env(value: object | None) -> str:
    """Return a stable comma-separated display/storage value for languages."""
    return ", ".join(parse_realtime_languages(value))


def language_code_for_realtime_transcription(language: str | None) -> str | None:
    """Map a user-facing language label/code to an ISO-style transcription hint."""
    if not language:
        return None
    normalized = _fold_language_label(language).replace("_", "-")
    if not normalized:
        return None
    if normalized in _LANGUAGE_CODE_ALIASES:
        return _LANGUAGE_CODE_ALIASES[normalized]

    # Accept already-code-like values such as en-US by using the base language.
    base, _, _region = normalized.partition("-")
    if base in _LANGUAGE_CODE_ALIASES:
        return _LANGUAGE_CODE_ALIASES[base]
    if 2 <= len(base) <= 3 and base.isalpha():
        return base
    return None


def get_realtime_languages() -> list[str]:
    """Return the runtime language preference list from config."""
    return parse_realtime_languages(getattr(config, "REALTIME_LANGUAGES", DEFAULT_REALTIME_LANGUAGES))


def get_primary_realtime_language_code() -> str | None:
    """Return the transcription hint code for the first configured language."""
    return language_code_for_realtime_transcription(get_realtime_languages()[0])


def _collect_profile_names(profiles_root: Path) -> set[str]:
    """Return profile folder names from a profiles root directory."""
    if not profiles_root.exists() or not profiles_root.is_dir():
        return set()
    return {p.name for p in profiles_root.iterdir() if p.is_dir()}


def _collect_tool_module_names(tools_root: Path) -> set[str]:
    """Return tool module names from a tools directory."""
    if not tools_root.exists() or not tools_root.is_dir():
        return set()
    ignored = {"__init__", "core_tools"}
    return {
        p.stem
        for p in tools_root.glob("*.py")
        if p.is_file() and p.stem not in ignored
    }


def _raise_on_name_collisions(
    *,
    label: str,
    external_root: Path,
    internal_root: Path,
    external_names: set[str],
    internal_names: set[str],
) -> None:
    """Raise with a clear message when external/internal names collide."""
    collisions = sorted(external_names & internal_names)
    if not collisions:
        return

    raise RuntimeError(
        f"Config.__init__(): Ambiguous {label} names found in both external and built-in libraries: {collisions}. "
        f"External {label} root: {external_root}. Built-in {label} root: {internal_root}. "
        f"Please rename the conflicting external {label}(s) to continue."
    )


# Validate LOCKED_PROFILE at startup
if LOCKED_PROFILE is not None:
    _profiles_dir = DEFAULT_PROFILES_DIRECTORY
    _profile_path = _profiles_dir / LOCKED_PROFILE
    _instructions_file = _profile_path / "instructions.txt"
    if not _profile_path.is_dir():
        print(f"Error: LOCKED_PROFILE '{LOCKED_PROFILE}' does not exist in {_profiles_dir}", file=sys.stderr)
        sys.exit(1)
    if not _instructions_file.is_file():
        print(f"Error: LOCKED_PROFILE '{LOCKED_PROFILE}' has no instructions.txt", file=sys.stderr)
        sys.exit(1)

_skip_dotenv = _env_flag("REACHY_MINI_SKIP_DOTENV", default=False)

if _skip_dotenv:
    logger.info("Skipping .env loading because REACHY_MINI_SKIP_DOTENV is set")
else:
    # Locate .env file (search upward from current working directory)
    dotenv_path = find_dotenv(usecwd=True)

    if dotenv_path:
        # Load .env and override environment variables
        load_dotenv(dotenv_path=dotenv_path, override=True)
        logger.info(f"Configuration loaded from {dotenv_path}")
    else:
        logger.warning("No .env file found, using environment variables")


class Config:
    """Configuration class for the conversation app."""

    # Required
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # The key is downloaded in console.py if needed

    # Optional
    MODEL_NAME = os.getenv("MODEL_NAME", "gpt-realtime")
    WAKE_ENABLED = _env_flag("HEY_ROBO_WAKE_ENABLED", default=True)
    WAKE_PHRASE = os.getenv("HEY_ROBO_WAKE_PHRASE", "HEY ROBO")
    WAKE_ENGINE = os.getenv("HEY_ROBO_WAKE_ENGINE", "vosk")
    WAKE_MODEL_PATH = os.getenv("HEY_ROBO_WAKE_MODEL_PATH", "")
    WAKE_SAMPLE_RATE = _env_int("HEY_ROBO_WAKE_SAMPLE_RATE", 16_000)
    WAKE_ACTIVATION_MIN_INTERVAL_SECONDS = _env_float("HEY_ROBO_WAKE_ACTIVATION_MIN_INTERVAL_SECONDS", 2.0)
    WAKE_SESSION_TIMEOUT_SECONDS = _env_float("HEY_ROBO_WAKE_SESSION_TIMEOUT_SECONDS", 45.0)
    STATE_POSES_ENABLED = _env_flag("HEY_ROBO_STATE_POSES_ENABLED", default=True)
    STANDBY_POSE_MODE = os.getenv("HEY_ROBO_STANDBY_POSE_MODE", "sleep_off")
    STANDBY_DISABLE_MOTORS = _env_flag("HEY_ROBO_STANDBY_DISABLE_MOTORS", default=True)
    ACTIVE_POSE_PITCH_DEGREES = _env_float("HEY_ROBO_ACTIVE_POSE_PITCH_DEGREES", -18.0)
    STANDBY_POSE_PITCH_DEGREES = _env_float("HEY_ROBO_STANDBY_POSE_PITCH_DEGREES", 24.0)
    ACTIVE_POSE_DURATION_SECONDS = _env_float("HEY_ROBO_ACTIVE_POSE_DURATION_SECONDS", 0.65)
    STANDBY_POSE_DURATION_SECONDS = _env_float("HEY_ROBO_STANDBY_POSE_DURATION_SECONDS", 0.9)
    REALTIME_VOICE = os.getenv("HEY_ROBO_REALTIME_VOICE", os.getenv("OPENAI_REALTIME_VOICE", "cedar"))
    REALTIME_LANGUAGES = parse_realtime_languages(os.getenv("HEY_ROBO_REALTIME_LANGUAGES"))
    CODEX_RELAY_URL = os.getenv("HEY_ROBO_CODEX_RELAY_URL", "http://127.0.0.1:8766")
    CODEX_RELAY_TOKEN = os.getenv("HEY_ROBO_CODEX_RELAY_TOKEN", "")
    CODEX_DEFAULT_WORKSPACE = os.getenv("HEY_ROBO_CODEX_DEFAULT_WORKSPACE", "current")
    HF_HOME = os.getenv("HF_HOME", "./cache")
    LOCAL_VISION_MODEL = os.getenv("LOCAL_VISION_MODEL", "HuggingFaceTB/SmolVLM2-2.2B-Instruct")
    HF_TOKEN = os.getenv("HF_TOKEN")  # Optional, falls back to hf auth login if not set

    logger.debug(f"Model: {MODEL_NAME}, HF_HOME: {HF_HOME}, Vision Model: {LOCAL_VISION_MODEL}")

    _profiles_directory_env = os.getenv("REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY")
    PROFILES_DIRECTORY = (
        Path(_profiles_directory_env) if _profiles_directory_env else Path(__file__).parent / "profiles"
    )
    _tools_directory_env = os.getenv("REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY")
    TOOLS_DIRECTORY = Path(_tools_directory_env) if _tools_directory_env else None
    AUTOLOAD_EXTERNAL_TOOLS = _env_flag("AUTOLOAD_EXTERNAL_TOOLS", default=False)
    REACHY_MINI_CUSTOM_PROFILE = LOCKED_PROFILE or os.getenv("REACHY_MINI_CUSTOM_PROFILE")

    logger.debug(f"Custom Profile: {REACHY_MINI_CUSTOM_PROFILE}")

    def __init__(self) -> None:
        """Initialize the configuration."""
        if self.REACHY_MINI_CUSTOM_PROFILE and self.PROFILES_DIRECTORY != DEFAULT_PROFILES_DIRECTORY:
            selected_profile_path = self.PROFILES_DIRECTORY / self.REACHY_MINI_CUSTOM_PROFILE
            if not selected_profile_path.is_dir():
                available_profiles = sorted(_collect_profile_names(self.PROFILES_DIRECTORY))
                raise RuntimeError(
                    "Config.__init__(): Selected profile "
                    f"'{self.REACHY_MINI_CUSTOM_PROFILE}' was not found in external profiles root "
                    f"{self.PROFILES_DIRECTORY}. "
                    f"Available external profiles: {available_profiles}. "
                    "Either set 'REACHY_MINI_CUSTOM_PROFILE' to one of the available external profiles "
                    "or unset 'REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY' to use built-in profiles."
                )

        if self.PROFILES_DIRECTORY != DEFAULT_PROFILES_DIRECTORY:
            external_profiles = _collect_profile_names(self.PROFILES_DIRECTORY)
            internal_profiles = _collect_profile_names(DEFAULT_PROFILES_DIRECTORY)
            _raise_on_name_collisions(
                label="profile",
                external_root=self.PROFILES_DIRECTORY,
                internal_root=DEFAULT_PROFILES_DIRECTORY,
                external_names=external_profiles,
                internal_names=internal_profiles,
            )

        if self.TOOLS_DIRECTORY is not None:
            builtin_tools_root = Path(__file__).parent / "tools"
            external_tools = _collect_tool_module_names(self.TOOLS_DIRECTORY)
            internal_tools = _collect_tool_module_names(builtin_tools_root)
            _raise_on_name_collisions(
                label="tool",
                external_root=self.TOOLS_DIRECTORY,
                internal_root=builtin_tools_root,
                external_names=external_tools,
                internal_names=internal_tools,
            )

        if self.PROFILES_DIRECTORY != DEFAULT_PROFILES_DIRECTORY:
            logger.warning(
                "Environment variable 'REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY' is set. "
                "Profiles (instructions.txt, ...) will be loaded from %s.",
                self.PROFILES_DIRECTORY,
            )
        else:
            logger.info(
                "'REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY' is not set. "
                "Using built-in profiles from %s.",
                DEFAULT_PROFILES_DIRECTORY,
            )

        if self.TOOLS_DIRECTORY is not None:
            logger.warning(
                "Environment variable 'REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY' is set. "
                "External tools will be loaded from %s.",
                self.TOOLS_DIRECTORY,
            )
        else:
            logger.info(
                "'REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY' is not set. "
                "Using built-in shared tools only."
            )


config = Config()


def set_custom_profile(profile: str | None) -> None:
    """Update the selected custom profile at runtime and expose it via env.

    This ensures modules that read `config` and code that inspects the
    environment see a consistent value.
    """
    if LOCKED_PROFILE is not None:
        return
    try:
        config.REACHY_MINI_CUSTOM_PROFILE = profile
    except Exception:
        pass
    try:
        import os as _os

        if profile:
            _os.environ["REACHY_MINI_CUSTOM_PROFILE"] = profile
        else:
            # Remove to reflect default
            _os.environ.pop("REACHY_MINI_CUSTOM_PROFILE", None)
    except Exception:
        pass
