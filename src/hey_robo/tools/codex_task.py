"""Realtime tool for dispatching tasks to the local Codex relay."""

from __future__ import annotations
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict
from urllib.parse import urljoin

from hey_robo.config import config
from hey_robo.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class DispatchCodexTask(Tool):
    """Dispatch a coding task to the local Codex relay."""

    name = "dispatch_codex_task"
    description = (
        "Send a bounded coding or repository task to the local Codex relay. "
        "Use this only after you know which configured workspace should be changed. "
        "The relay will create a branch before allowing Codex to edit code."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Allowlisted workspace id configured on the relay. Use 'current' if the user did not specify another configured id.",
            },
            "task": {
                "type": "string",
                "description": "The concrete task Codex should perform. Include enough context for a coding agent to act.",
            },
            "branch_name": {
                "type": "string",
                "description": "Optional branch name. The relay will prefix codex/ if needed.",
            },
        },
        "required": ["workspace_id", "task"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Create a task on the configured relay."""
        relay_url = (getattr(config, "CODEX_RELAY_URL", "") or "").strip()
        relay_token = (getattr(config, "CODEX_RELAY_TOKEN", "") or "").strip()
        if not relay_url:
            return {"error": "Codex relay URL is not configured in app settings."}
        if not relay_token:
            return {"error": "Codex relay token is not configured in app settings."}

        workspace_id = str(kwargs.get("workspace_id") or getattr(config, "CODEX_DEFAULT_WORKSPACE", "current"))
        task = str(kwargs.get("task") or "").strip()
        branch_name = kwargs.get("branch_name")
        if not task:
            return {"error": "A concrete Codex task is required."}

        payload: dict[str, Any] = {
            "workspace_id": workspace_id,
            "task": task,
        }
        if isinstance(branch_name, str) and branch_name.strip():
            payload["branch_name"] = branch_name.strip()

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            urljoin(relay_url.rstrip("/") + "/", "tasks"),
            data=body,
            headers={
                "Authorization": f"Bearer {relay_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                response_body = response.read().decode("utf-8")
                data = json.loads(response_body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            logger.warning("Codex relay rejected task: %s %s", exc.code, detail)
            return {"error": f"Codex relay rejected the task: HTTP {exc.code}", "detail": detail[:500]}
        except Exception as exc:
            logger.warning("Codex relay unavailable: %s", exc)
            return {"error": f"Codex relay unavailable: {type(exc).__name__}: {exc}"}

        return {
            "status": "dispatched",
            "task_id": data.get("task_id"),
            "relay_state": data.get("state"),
            "workspace_id": data.get("workspace_id", workspace_id),
            "branch_name": data.get("branch_name"),
            "message": "Codex task dispatched. Use task_status for local tool state, or ask the relay for task details.",
        }
