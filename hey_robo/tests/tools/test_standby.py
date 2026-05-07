"""Tests for the standby Realtime tool."""

import pytest

from hey_robo.tools.standby import EnterStandby
from hey_robo.tools.core_tools import ToolDependencies


@pytest.mark.asyncio
async def test_enter_standby_returns_request_without_closing_directly() -> None:
    """The tool returns a structured standby request for the handler to apply."""
    tool = EnterStandby()
    deps = ToolDependencies(reachy_mini=None, movement_manager=None)  # type: ignore[arg-type]

    result = await tool(deps, reason="user said go to sleep")

    assert result["standby_requested"] is True
    assert result["status"] == "standby_requested"
    assert result["reason"] == "user said go to sleep"
