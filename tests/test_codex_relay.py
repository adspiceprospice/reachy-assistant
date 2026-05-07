import os
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from hey_robo.codex_relay import RelaySettings, create_app


def _git(args, cwd: Path):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_codex_relay_creates_branch_before_running_codex(tmp_path, monkeypatch):
    """Create a codex branch before invoking the configured Codex binary."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _git(["init"], workspace)
    _git(["config", "user.email", "test@example.com"], workspace)
    _git(["config", "user.name", "Test User"], workspace)
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "README.md"], workspace)
    _git(["commit", "-m", "init"], workspace)

    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/bin/sh\necho '{\"type\":\"assistant\",\"message\":\"done\"}'\n", encoding="utf-8")
    fake_codex.chmod(0o755)

    settings = RelaySettings(
        token="secret",
        workspaces={"current": workspace},
        codex_binary=str(fake_codex),
        task_log_dir=tmp_path / "logs",
    )
    client = TestClient(create_app(settings))

    response = client.post(
        "/tasks",
        headers={"Authorization": "Bearer secret"},
        json={"workspace_id": "current", "task": "Update README"},
    )

    assert response.status_code == 200
    task_id = response.json()["task_id"]
    task = client.get(f"/tasks/{task_id}", headers={"Authorization": "Bearer secret"}).json()
    assert task["state"] == "succeeded"
    assert task["branch_name"].startswith("codex/hey-robo-")

    branch = _git(["branch", "--show-current"], workspace).stdout.strip()
    assert branch == task["branch_name"]


def test_codex_relay_rejects_dirty_workspace(tmp_path):
    """Reject automated edits when the target workspace is dirty."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _git(["init"], workspace)
    _git(["config", "user.email", "test@example.com"], workspace)
    _git(["config", "user.name", "Test User"], workspace)
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "README.md"], workspace)
    _git(["commit", "-m", "init"], workspace)
    (workspace / "README.md").write_text("dirty\n", encoding="utf-8")

    settings = RelaySettings(
        token="secret",
        workspaces={"current": workspace},
        codex_binary=os.devnull,
        task_log_dir=tmp_path / "logs",
    )
    client = TestClient(create_app(settings))

    response = client.post(
        "/tasks",
        headers={"Authorization": "Bearer secret"},
        json={"workspace_id": "current", "task": "Update README"},
    )

    task_id = response.json()["task_id"]
    task = client.get(f"/tasks/{task_id}", headers={"Authorization": "Bearer secret"}).json()
    assert task["state"] == "failed"
    assert "uncommitted changes" in task["error"]
