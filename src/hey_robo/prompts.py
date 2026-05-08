import re
import sys
import logging
from pathlib import Path

from hey_robo.config import DEFAULT_PROFILES_DIRECTORY, config, get_realtime_languages


logger = logging.getLogger(__name__)


PROMPTS_LIBRARY_DIRECTORY = Path(__file__).parent / "prompts"
INSTRUCTIONS_FILENAME = "instructions.txt"
VOICE_FILENAME = "voice.txt"


def _format_language_preferences() -> str:
    """Build the runtime language instruction block from app settings."""
    languages = get_realtime_languages()
    primary = languages[0]
    if len(languages) == 1:
        language_line = primary
    else:
        language_line = ", ".join(
            f"{idx + 1}. {language}" for idx, language in enumerate(languages)
        )

    return (
        "## Language\n"
        f"- Expected user languages, ordered by likelihood: {language_line}.\n"
        f"- Start in {primary}. Treat {primary} as the default language when the first user audio after the wake phrase is ambiguous, silent, or noisy.\n"
        "- Speak only the expected languages above unless the user clearly asks for another language by name.\n"
        "- Do not switch to Chinese, Spanish, or any other unrelated language from background noise, short sounds, or uncertain transcription.\n"
        "- Once the user clearly speaks one of the expected languages, respond in that same language unless they ask otherwise.\n\n"
        "## Unclear Audio\n"
        f"- If recognition appears unrelated to the expected languages, ask briefly in {primary} for the user to repeat."
    )


def _expand_prompt_includes(content: str) -> str:
    """Expand [<name>] placeholders with content from prompts library files.

    Args:
        content: The template content with [<name>] placeholders

    Returns:
        Expanded content with placeholders replaced by file contents

    """
    # Pattern to match [<name>] where name is a valid file stem (alphanumeric, underscores, hyphens)
    # pattern = re.compile(r'^\[([a-zA-Z0-9_-]+)\]$')
    # Allow slashes for subdirectories
    pattern = re.compile(r'^\[([a-zA-Z0-9/_-]+)\]$')

    lines = content.split('\n')
    expanded_lines = []

    for line in lines:
        stripped = line.strip()
        match = pattern.match(stripped)

        if match:
            # Extract the name from [<name>]
            template_name = match.group(1)
            template_file = PROMPTS_LIBRARY_DIRECTORY / f"{template_name}.txt"

            try:
                if template_file.exists():
                    template_content = template_file.read_text(encoding="utf-8").rstrip()
                    expanded_lines.append(template_content)
                    logger.debug("Expanded template: [%s]", template_name)
                else:
                    logger.warning("Template file not found: %s, keeping placeholder", template_file)
                    expanded_lines.append(line)
            except Exception as e:
                logger.warning("Failed to read template '%s': %s, keeping placeholder", template_name, e)
                expanded_lines.append(line)
        else:
            expanded_lines.append(line)

    return '\n'.join(expanded_lines)


def get_session_instructions() -> str:
    """Get session instructions, loading from REACHY_MINI_CUSTOM_PROFILE if set."""
    profile = config.REACHY_MINI_CUSTOM_PROFILE
    if not profile:
        logger.info(f"Loading default prompt from {PROMPTS_LIBRARY_DIRECTORY / 'default_prompt.txt'}")
        instructions_file = PROMPTS_LIBRARY_DIRECTORY / "default_prompt.txt"
    else:
        if config.PROFILES_DIRECTORY != DEFAULT_PROFILES_DIRECTORY:
            logger.info(
                "Loading prompt from external profile '%s' (root=%s)",
                profile,
                config.PROFILES_DIRECTORY,
            )
        else:
            logger.info(f"Loading prompt from profile '{profile}'")
        instructions_file = config.PROFILES_DIRECTORY / profile / INSTRUCTIONS_FILENAME

    try:
        if instructions_file.exists():
            instructions = instructions_file.read_text(encoding="utf-8").strip()
            if instructions:
                # Expand [<name>] placeholders with content from prompts library
                expanded_instructions = _expand_prompt_includes(instructions)
                return f"{expanded_instructions}\n\n{_format_language_preferences()}"
            logger.error(f"Profile '{profile}' has empty {INSTRUCTIONS_FILENAME}")
            sys.exit(1)
        logger.error(f"Profile {profile} has no {INSTRUCTIONS_FILENAME}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to load instructions from profile '{profile}': {e}")
        sys.exit(1)


def get_session_voice(default: str | None = None) -> str:
    """Resolve the voice to use for the session.

    If a custom profile is selected and contains a voice.txt, return its
    trimmed content; otherwise return the configured app default.
    """
    resolved_default = default or getattr(config, "REALTIME_VOICE", "cedar") or "cedar"
    profile = config.REACHY_MINI_CUSTOM_PROFILE
    if not profile:
        return resolved_default
    try:
        voice_file = config.PROFILES_DIRECTORY / profile / VOICE_FILENAME
        if voice_file.exists():
            voice = voice_file.read_text(encoding="utf-8").strip()
            return voice or resolved_default
    except Exception:
        pass
    return resolved_default
