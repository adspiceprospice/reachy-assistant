import sys
import json
import types
from unittest.mock import MagicMock

import pytest


reachy_mini_stub = types.ModuleType("reachy_mini")
reachy_mini_stub.ReachyMini = object
sys.modules.setdefault("reachy_mini", reachy_mini_stub)

from hey_robo.config import config  # noqa: E402
from hey_robo.tools.codex_task import DispatchCodexTask  # noqa: E402


@pytest.mark.asyncio
async def test_codex_task_requires_relay_token(monkeypatch):
    """Refuse dispatch when the relay token is not configured."""
    monkeypatch.setattr(config, "CODEX_RELAY_URL", "http://127.0.0.1:8766", raising=False)
    monkeypatch.setattr(config, "CODEX_RELAY_TOKEN", "", raising=False)

    result = await DispatchCodexTask()(MagicMock(), workspace_id="current", task="change README")

    assert "relay token" in result["error"]


@pytest.mark.asyncio
async def test_codex_task_posts_to_relay(monkeypatch):
    """Send authenticated task payloads to the configured relay."""
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"task_id": "abc", "state": "queued", "workspace_id": "current"}).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode())
        captured["authorization"] = request.headers["Authorization"]
        return FakeResponse()

    monkeypatch.setattr(config, "CODEX_RELAY_URL", "http://relay.local:8766", raising=False)
    monkeypatch.setattr(config, "CODEX_RELAY_TOKEN", "secret", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = await DispatchCodexTask()(MagicMock(), workspace_id="current", task="change README")

    assert result["status"] == "dispatched"
    assert captured["url"] == "http://relay.local:8766/tasks"
    assert captured["authorization"] == "Bearer secret"
    assert captured["body"]["task"] == "change README"


@pytest.mark.asyncio
async def test_codex_task_uses_default_workspace(monkeypatch):
    """Use the configured default workspace when the model omits workspace_id."""
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"task_id": "abc", "state": "queued", "workspace_id": "smoke"}).encode()

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse()

    monkeypatch.setattr(config, "CODEX_RELAY_URL", "http://relay.local:8766", raising=False)
    monkeypatch.setattr(config, "CODEX_RELAY_TOKEN", "secret", raising=False)
    monkeypatch.setattr(config, "CODEX_DEFAULT_WORKSPACE", "smoke", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = await DispatchCodexTask()(MagicMock(), task="change README")

    assert result["workspace_id"] == "smoke"
    assert captured["body"]["workspace_id"] == "smoke"
