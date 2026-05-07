"""In-process live log stream for the HeyRobo settings dashboard."""

from __future__ import annotations
import re
import asyncio
import logging
import threading
from typing import Any
from datetime import datetime, timezone
from itertools import count
from collections import deque
from dataclasses import dataclass


_LOG_ID_COUNTER = count(1)
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "sk-...redacted"),
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"), r"\1[redacted]"),
    (
        re.compile(
            r"(?i)((?:OPENAI_API_KEY|HEY_ROBO_CODEX_RELAY_TOKEN|codex_relay_token|relay_token|api_key)"
            r"\s*[:=]\s*)[^\s,;]+",
        ),
        r"\1[redacted]",
    ),
)


def sanitize_log_message(value: str) -> str:
    """Remove API keys and bearer tokens from log text before it reaches the UI."""
    sanitized = value
    for pattern, replacement in _SECRET_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


@dataclass(frozen=True)
class _Subscriber:
    """Asyncio subscriber bound to the loop that opened the SSE stream."""

    queue: asyncio.Queue[dict[str, Any]]
    loop: asyncio.AbstractEventLoop


class LiveLogHub:
    """Store recent log records and broadcast new ones to dashboard clients."""

    def __init__(self, max_events: int = 500, subscriber_queue_size: int = 200) -> None:
        """Create a bounded hub for recent log storage and SSE subscribers."""
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._subscribers: set[_Subscriber] = set()
        self._subscriber_queue_size = subscriber_queue_size
        self._lock = threading.RLock()

    def add_record(self, record: logging.LogRecord) -> None:
        """Append a logging record after formatting and sanitization."""
        message = record.getMessage()
        if record.exc_info:
            formatter = logging.Formatter()
            message = f"{message}\n{formatter.formatException(record.exc_info)}"
        self.add_event(
            level=record.levelname,
            logger_name=record.name,
            source=f"{record.name}:{record.lineno}",
            message=message,
        )

    def add_event(self, *, level: str, logger_name: str, source: str, message: str) -> None:
        """Append a structured event and notify active subscribers."""
        event = {
            "id": next(_LOG_ID_COUNTER),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": level.upper(),
            "logger": logger_name,
            "source": source,
            "message": sanitize_log_message(message),
        }
        with self._lock:
            self._events.append(event)
            subscribers = tuple(self._subscribers)

        for subscriber in subscribers:
            self._deliver(subscriber, event)

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        """Return recent log events, oldest first."""
        safe_limit = max(1, min(int(limit), self._events.maxlen or limit))
        with self._lock:
            return list(self._events)[-safe_limit:]

    def subscribe(self, loop: asyncio.AbstractEventLoop | None = None) -> _Subscriber:
        """Subscribe the current asyncio loop to future log events."""
        subscriber = _Subscriber(
            queue=asyncio.Queue(maxsize=self._subscriber_queue_size),
            loop=loop or asyncio.get_running_loop(),
        )
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: _Subscriber) -> None:
        """Remove a subscriber from future broadcasts."""
        with self._lock:
            self._subscribers.discard(subscriber)

    def _deliver(self, subscriber: _Subscriber, event: dict[str, Any]) -> None:
        """Deliver an event to a subscriber without blocking logging threads."""

        def _put() -> None:
            try:
                subscriber.queue.put_nowait(event)
                return
            except asyncio.QueueFull:
                pass

            try:
                subscriber.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                subscriber.queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

        try:
            subscriber.loop.call_soon_threadsafe(_put)
        except RuntimeError:
            self.unsubscribe(subscriber)


class LiveLogHandler(logging.Handler):
    """Logging handler that feeds the shared live log hub."""

    def __init__(self, hub: LiveLogHub) -> None:
        """Create a handler attached to a live log hub."""
        super().__init__(level=logging.DEBUG)
        self.hub = hub

    def emit(self, record: logging.LogRecord) -> None:
        """Forward one logging record to the live log hub."""
        try:
            self.hub.add_record(record)
        except Exception:
            self.handleError(record)


_LIVE_LOG_HUB = LiveLogHub()


def get_live_log_hub() -> LiveLogHub:
    """Return the process-wide log hub."""
    return _LIVE_LOG_HUB


def install_live_log_handler() -> None:
    """Attach the live log handler to the root logger once."""
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, LiveLogHandler):
            return
    root.addHandler(LiveLogHandler(_LIVE_LOG_HUB))
