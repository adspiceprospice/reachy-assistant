"""Tests for the live settings-dashboard log stream."""

import asyncio
import logging

import pytest

from hey_robo.log_stream import LiveLogHub, sanitize_log_message


def test_sanitize_log_message_redacts_known_secret_shapes() -> None:
    """Do not expose OpenAI keys or relay tokens in streamed logs."""
    raw = (
        "Authorization: Bearer relay-secret OPENAI_API_KEY=sk-testsecret12345 "
        "HEY_ROBO_CODEX_RELAY_TOKEN=local-token"
    )
    sanitized = sanitize_log_message(raw)

    assert "relay-secret" not in sanitized
    assert "sk-testsecret12345" not in sanitized
    assert "local-token" not in sanitized
    assert "Bearer [redacted]" in sanitized
    assert "OPENAI_API_KEY=[redacted]" in sanitized


def test_live_log_hub_keeps_bounded_recent_events() -> None:
    """Recent logs stay bounded and preserve the newest entries."""
    hub = LiveLogHub(max_events=2)

    hub.add_event(level="INFO", logger_name="one", source="one:1", message="first")
    hub.add_event(level="WARNING", logger_name="two", source="two:2", message="second")
    hub.add_event(level="ERROR", logger_name="three", source="three:3", message="third")

    recent = hub.recent(limit=10)
    assert [event["message"] for event in recent] == ["second", "third"]
    assert [event["level"] for event in recent] == ["WARNING", "ERROR"]


def test_live_log_hub_formats_logging_records() -> None:
    """A logging record is converted into the dashboard payload shape."""
    hub = LiveLogHub(max_events=5)
    record = logging.LogRecord(
        name="hey_robo.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=42,
        msg="Tool call token=%s",
        args=("sk-recordsecret12345",),
        exc_info=None,
    )

    hub.add_record(record)

    [event] = hub.recent()
    assert event["level"] == "INFO"
    assert event["logger"] == "hey_robo.test"
    assert event["source"] == "hey_robo.test:42"
    assert "sk-recordsecret12345" not in event["message"]


@pytest.mark.asyncio
async def test_live_log_hub_broadcasts_to_async_subscribers() -> None:
    """SSE subscribers receive new records on their owning event loop."""
    hub = LiveLogHub(max_events=5)
    subscriber = hub.subscribe(asyncio.get_running_loop())
    try:
        hub.add_event(level="INFO", logger_name="hey_robo.test", source="test:1", message="hello")
        event = await asyncio.wait_for(subscriber.queue.get(), timeout=1.0)
    finally:
        hub.unsubscribe(subscriber)

    assert event["message"] == "hello"
    assert event["level"] == "INFO"
