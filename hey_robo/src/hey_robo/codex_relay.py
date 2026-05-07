"""Local Codex relay for Hey Robo.

The robot app never exposes arbitrary shell execution. It sends bounded task
requests here; this service authenticates the request, maps a workspace id to
an allowlisted path, creates or selects a task branch, and then runs
``codex exec`` non-interactively.
"""

from __future__ import annotations
import os
import uuid
import asyncio
import logging
import subprocess
from enum import Enum
from typing import Any, Annotated
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import field, dataclass

from fastapi import Header, FastAPI, HTTPException, BackgroundTasks
from pydantic import Field, BaseModel


logger = logging.getLogger(__name__)


class TaskState(str, Enum):
    """Lifecycle state for a relayed Codex task."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TaskRequest(BaseModel):
    """Request body for creating a Codex task."""

    workspace_id: str = Field(default="current", min_length=1, max_length=80)
    task: str = Field(min_length=1, max_length=12000)
    branch_name: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=80)
    sandbox: str = Field(default="workspace-write", pattern="^(read-only|workspace-write|danger-full-access)$")


class TaskResponse(BaseModel):
    """Public task record returned by the relay."""

    task_id: str
    state: TaskState
    workspace_id: str
    branch_name: str | None = None
    created_at: str
    updated_at: str
    summary: str | None = None
    error: str | None = None


@dataclass
class RelaySettings:
    """Runtime configuration for the relay service."""

    token: str
    workspaces: dict[str, Path]
    codex_binary: str = "codex"
    task_log_dir: Path = field(default_factory=lambda: Path.home() / ".hey_robo" / "codex_tasks")


@dataclass
class TaskRecord:
    """Internal mutable task record."""

    task_id: str
    state: TaskState
    workspace_id: str
    workspace_path: Path
    prompt: str
    created_at: str
    updated_at: str
    branch_name: str | None = None
    summary: str | None = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""

    def public(self) -> TaskResponse:
        """Return the public representation."""
        return TaskResponse(
            task_id=self.task_id,
            state=self.state,
            workspace_id=self.workspace_id,
            branch_name=self.branch_name,
            created_at=self.created_at,
            updated_at=self.updated_at,
            summary=self.summary,
            error=self.error,
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_workspaces(raw: str | None, *, default_cwd: Path | None = None) -> dict[str, Path]:
    """Parse ``id=/abs/path`` workspace entries.

    Entries may be separated by semicolons or newlines. When unset, the relay
    exposes only the current working directory as ``current``.
    """
    if not raw or not raw.strip():
        return {"current": (default_cwd or Path.cwd()).resolve()}

    workspaces: dict[str, Path] = {}
    normalized = raw.replace("\n", ";")
    for entry in normalized.split(";"):
        item = entry.strip()
        if not item:
            continue
        workspace_id, sep, path_raw = item.partition("=")
        if not sep:
            raise ValueError(f"Invalid workspace entry {item!r}; expected id=/abs/path")
        workspace_id = workspace_id.strip()
        path = Path(path_raw.strip()).expanduser().resolve()
        if not workspace_id:
            raise ValueError(f"Invalid empty workspace id in {item!r}")
        workspaces[workspace_id] = path
    if not workspaces:
        raise ValueError("No valid workspaces configured")
    return workspaces


def load_settings() -> RelaySettings:
    """Load relay settings from environment variables."""
    token = os.getenv("HEY_ROBO_RELAY_TOKEN") or os.getenv("HEY_ROBO_CODEX_RELAY_TOKEN") or ""
    workspaces = _parse_workspaces(os.getenv("HEY_ROBO_CODEX_WORKSPACES"))
    codex_binary = os.getenv("HEY_ROBO_CODEX_BINARY", "codex")
    log_dir = Path(os.getenv("HEY_ROBO_CODEX_TASK_LOG_DIR", str(Path.home() / ".hey_robo" / "codex_tasks")))
    return RelaySettings(token=token, workspaces=workspaces, codex_binary=codex_binary, task_log_dir=log_dir)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )


def _ensure_clean_git_workspace(workspace: Path) -> None:
    result = _run_git(["rev-parse", "--show-toplevel"], workspace)
    if result.returncode != 0:
        raise RuntimeError(f"Workspace is not a git repository: {workspace}")

    status = _run_git(["status", "--porcelain"], workspace)
    if status.returncode != 0:
        raise RuntimeError(status.stderr.strip() or "Failed to inspect git status")
    if status.stdout.strip():
        raise RuntimeError("Workspace has uncommitted changes; refusing automated Codex edits")


def _checkout_task_branch(workspace: Path, requested_branch: str | None, task_id: str) -> str:
    branch = requested_branch or f"codex/hey-robo-{task_id[:8]}"
    if not branch.startswith("codex/"):
        branch = f"codex/{branch}"

    exists = _run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], workspace)
    if exists.returncode == 0:
        checkout = _run_git(["checkout", branch], workspace)
    else:
        checkout = _run_git(["checkout", "-b", branch], workspace)
    if checkout.returncode != 0:
        raise RuntimeError(checkout.stderr.strip() or f"Failed to checkout branch {branch}")
    return branch


async def _run_codex_task(record: TaskRecord, request: TaskRequest, settings: RelaySettings) -> None:
    record.state = TaskState.RUNNING
    record.updated_at = _utc_now()
    settings.task_log_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.task_log_dir / f"{record.task_id}.log"

    try:
        _ensure_clean_git_workspace(record.workspace_path)
        record.branch_name = _checkout_task_branch(record.workspace_path, request.branch_name, record.task_id)
        record.updated_at = _utc_now()

        prompt = (
            "You are running from the Hey Robo local Codex relay. "
            "Make the requested code changes directly in this workspace. "
            "Keep edits scoped, do not touch secrets, and summarize changed files.\n\n"
            f"Task:\n{record.prompt}"
        )
        cmd = [
            settings.codex_binary,
            "exec",
            "--cd",
            str(record.workspace_path),
            "--json",
            "--sandbox",
            request.sandbox,
        ]
        if request.model:
            cmd.extend(["--model", request.model])
        cmd.append(prompt)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(record.workspace_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        record.stdout = stdout_bytes.decode("utf-8", errors="replace")
        record.stderr = stderr_bytes.decode("utf-8", errors="replace")
        log_path.write_text(record.stdout + "\n\n[stderr]\n" + record.stderr, encoding="utf-8")

        if process.returncode == 0:
            record.state = TaskState.SUCCEEDED
            record.summary = _summarize_codex_output(record.stdout)
        else:
            record.state = TaskState.FAILED
            record.error = record.stderr.strip() or f"codex exited with {process.returncode}"
    except Exception as exc:
        logger.exception("Codex task %s failed", record.task_id)
        record.state = TaskState.FAILED
        record.error = str(exc)
    finally:
        record.updated_at = _utc_now()


def _summarize_codex_output(stdout: str) -> str:
    """Return a compact task summary from Codex JSONL or plain output."""
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return "Codex completed without output."
    for line in reversed(lines):
        if "assistant" in line.lower() or "final" in line.lower() or "message" in line.lower():
            return line[:2000]
    return lines[-1][:2000]


def create_app(settings: RelaySettings | None = None) -> FastAPI:
    """Create the relay FastAPI app."""
    resolved_settings = settings or load_settings()
    tasks: dict[str, TaskRecord] = {}
    app = FastAPI(title="Hey Robo Codex Relay")

    def require_auth(authorization: str | None) -> None:
        if not resolved_settings.token:
            raise HTTPException(status_code=503, detail="relay_token_not_configured")
        expected = f"Bearer {resolved_settings.token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "workspaces": sorted(resolved_settings.workspaces.keys()),
            "requires_auth": bool(resolved_settings.token),
        }

    @app.post("/tasks", response_model=TaskResponse)
    async def create_task(
        request: TaskRequest,
        background_tasks: BackgroundTasks,
        authorization: Annotated[str | None, Header()] = None,
    ) -> TaskResponse:
        require_auth(authorization)
        workspace = resolved_settings.workspaces.get(request.workspace_id)
        if workspace is None:
            raise HTTPException(status_code=400, detail="workspace_not_allowed")
        if not workspace.exists():
            raise HTTPException(status_code=400, detail="workspace_missing")

        task_id = uuid.uuid4().hex
        now = _utc_now()
        record = TaskRecord(
            task_id=task_id,
            state=TaskState.QUEUED,
            workspace_id=request.workspace_id,
            workspace_path=workspace,
            prompt=request.task,
            created_at=now,
            updated_at=now,
        )
        tasks[task_id] = record
        background_tasks.add_task(_run_codex_task, record, request, resolved_settings)
        return record.public()

    @app.get("/tasks/{task_id}", response_model=TaskResponse)
    def get_task(task_id: str, authorization: Annotated[str | None, Header()] = None) -> TaskResponse:
        require_auth(authorization)
        record = tasks.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="task_not_found")
        return record.public()

    return app


app = create_app()


def main() -> None:
    """Run the relay server with uvicorn."""
    import uvicorn

    host = os.getenv("HEY_ROBO_RELAY_HOST", "127.0.0.1")
    port = int(os.getenv("HEY_ROBO_RELAY_PORT", "8766"))
    uvicorn.run("hey_robo.codex_relay:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
