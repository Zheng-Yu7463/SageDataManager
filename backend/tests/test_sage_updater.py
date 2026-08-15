from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "deploy" / "sage_updater.py"
SPEC = importlib.util.spec_from_file_location("sage_updater_test_module", MODULE_PATH)
assert SPEC and SPEC.loader
sage_updater = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sage_updater
SPEC.loader.exec_module(sage_updater)


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def create_repository_pair(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "clone", str(remote), str(worktree)], check=True, capture_output=True)
    git(worktree, "checkout", "-b", "main")
    git(worktree, "config", "user.email", "tester@example.org")
    git(worktree, "config", "user.name", "Test User")
    (worktree / "README.md").write_text("initial\n", encoding="utf-8")
    git(worktree, "add", "README.md")
    git(worktree, "commit", "-m", "initial")
    git(worktree, "push", "-u", "origin", "main")
    subprocess.run(
        ["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
    )
    return remote, worktree


def agent_config(tmp_path: Path, remote: Path, worktree: Path):
    return sage_updater.AgentConfig(
        repository=worktree,
        socket_path=tmp_path / "updater.sock",
        state_directory=tmp_path / "state",
        secret="s" * 64,
        expected_remote=str(remote),
    )


def test_remote_url_normalization_accepts_https_and_ssh_forms() -> None:
    assert sage_updater.normalize_remote_url(
        "git@github.com:Zheng-Yu7463/SageDataManager.git"
    ) == sage_updater.normalize_remote_url(
        "https://github.com/Zheng-Yu7463/SageDataManager/"
    )


def test_dotenv_reader_handles_comments_and_quotes(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# comment\nPOSTGRES_USER='sage user'\nPOSTGRES_DB=sage\n",
        encoding="utf-8",
    )
    assert sage_updater.read_dotenv(path) == {
        "POSTGRES_USER": "sage user",
        "POSTGRES_DB": "sage",
    }


def test_check_reports_new_remote_commits(tmp_path: Path) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    publisher = tmp_path / "publisher"
    subprocess.run(["git", "clone", str(remote), str(publisher)], check=True, capture_output=True)
    git(publisher, "config", "user.email", "publisher@example.org")
    git(publisher, "config", "user.name", "Publisher")
    (publisher / "CHANGELOG.md").write_text("new feature\n", encoding="utf-8")
    git(publisher, "add", "CHANGELOG.md")
    git(publisher, "commit", "-m", "add update feature")
    git(publisher, "push", "origin", "main")

    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    status = manager.check()

    assert status["state"] == "available"
    assert status["update_available"] is True
    assert status["behind_count"] == 1
    assert status["commits"][0]["subject"] == "add update feature"


def test_check_rejects_a_dirty_worktree(tmp_path: Path) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    (worktree / "local-note.txt").write_text("do not overwrite\n", encoding="utf-8")

    with pytest.raises(sage_updater.UpdateAgentError, match="未提交"):
        manager.check()

    assert manager.status()["state"] == "failed"


def test_snap_docker_uses_packaged_clients_without_the_snap_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docker_binary = tmp_path / "snap/docker/current/bin/docker"
    compose_binary = tmp_path / "snap/docker/current/usr/libexec/docker/cli-plugins/docker-compose"
    docker_binary.parent.mkdir(parents=True)
    compose_binary.parent.mkdir(parents=True)
    docker_binary.touch()
    compose_binary.touch()
    monkeypatch.setattr(sage_updater, "SNAP_DOCKER_BINARY", docker_binary)
    monkeypatch.setattr(sage_updater, "SNAP_COMPOSE_BINARY", compose_binary)

    docker_command, compose_command = sage_updater.resolve_container_commands("/snap/bin/docker")

    assert docker_command == (str(docker_binary),)
    assert compose_command == (str(compose_binary),)


def test_update_uses_the_commit_fetched_during_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    old_commit = git(worktree, "rev-parse", "HEAD")
    target_commit = "f" * 40
    commands: list[list[str]] = []

    monkeypatch.setattr(manager, "_capture_running_images", lambda: {})
    monkeypatch.setattr(manager, "_backup_database", lambda commit: tmp_path / "backup.dump")
    monkeypatch.setattr(manager, "_wait_for_health", lambda: None)
    monkeypatch.setattr(manager, "_git", lambda arguments: target_commit)
    monkeypatch.setattr(
        manager,
        "_inspect_repository",
        lambda fetch: {
            **manager.status(),
            "current_commit": target_commit,
            "latest_commit": target_commit,
            "update_available": False,
        },
    )

    def record_run(command: list[str], **kwargs):
        commands.append(command)
        return sage_updater.CommandResult(stdout="")

    monkeypatch.setattr(manager, "_run", record_run)
    manager._operation_lock.acquire()
    manager._perform_update(old_commit, target_commit)

    assert ["git", "merge", "--ff-only", target_commit] in commands
    assert not any(command[:2] == ["git", "pull"] for command in commands)


def test_run_reports_multiple_stderr_lines(tmp_path: Path) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))

    with pytest.raises(sage_updater.UpdateAgentError) as captured:
        manager._run(
            ["sh", "-c", "printf 'first detail\\nsecond detail\\n' >&2; exit 1"],
            timeout=10,
        )

    assert "first detail；second detail" in str(captured.value)


def test_installer_keeps_the_socket_mount_visible_after_agent_restart() -> None:
    repository = MODULE_PATH.parents[1]
    service = (repository / "deploy/sage-updater.service").read_text(encoding="utf-8")
    installer = (repository / "deploy/install-updater.sh").read_text(encoding="utf-8")

    assert "RuntimeDirectoryPreserve=yes" in service
    assert "docker compose up --build -d --force-recreate --no-deps backend" in installer
