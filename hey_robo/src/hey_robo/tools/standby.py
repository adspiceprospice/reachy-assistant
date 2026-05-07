"""Tool for returning HeyRobo to wake-word standby mode."""

from __future__ import annotations
import logging
from typing import Any, Dict

from hey_robo.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class EnterStandby(Tool):
    """Request wake-word standby mode."""

    name = "enter_standby"
    description = (
        "Return HeyRobo to low-power standby mode. Use this when the user asks you to sleep, "
        "stop listening, standby, go quiet, or wait for the wake phrase again."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Short reason for entering standby.",
            },
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Return a standby request for the Realtime handler to apply."""
        reason = str(kwargs.get("reason") or "User asked HeyRobo to go to sleep.").strip()
        logger.info("Tool call: enter_standby reason=%s", reason)
        return {
            "status": "standby_requested",
            "standby_requested": True,
            "reason": reason,
            "message": "Returning to wake-word standby.",
        }
