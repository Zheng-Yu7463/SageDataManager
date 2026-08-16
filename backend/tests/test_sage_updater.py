from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
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


def wait_for_manager_state(
    manager,
    terminal_states: set[str],
    timeout: float = 2,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.status()
        if status["state"] in terminal_states:
            return status
        time.sleep(0.01)
    raise AssertionError(f"更新代理未进入预期状态：{manager.status()}")


def prepare_checked_update(manager, target_commit: str) -> None:
    manager._replace_state(
        {
            **manager.status(),
            "state": "available",
            "latest_commit": target_commit,
            "checked_at": sage_updater.utc_now(),
            "update_available": True,
        }
    )


def test_committed_recovery_reports_successful_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    old_commit = git(worktree, "rev-parse", "HEAD")
    images = {
        "backend": {
            "image_name": "sage-data-manager-backend",
            "rollback_tag": "sage-data-manager-rollback-backend:operation-id",
        }
    }
    manager._replace_state(
        {
            **manager.status(),
            "state": "recovering",
            "phase": "interrupted_recovery",
            "interrupted_phase": "committed",
            "old_commit": old_commit,
            "target_commit": "f" * 40,
            "rollback_images": images,
            "backup_path": "backup.dump",
        }
    )
    restored: list[tuple[str, dict[str, dict[str, str]]]] = []

    def fail_new_release(*, expected_commit: str) -> None:
        raise sage_updater.UpdateAgentError(f"{expected_commit} 未就绪")

    monkeypatch.setattr(manager, "_wait_for_health", fail_new_release)
    monkeypatch.setattr(
        manager,
        "_rollback_application",
        lambda commit, rollback: restored.append((commit, rollback)),
    )
    monkeypatch.setattr(manager, "_cleanup_rollback_images", lambda rollback: None)
    monkeypatch.setattr(
        manager,
        "_inspect_repository",
        lambda fetch: {**manager.status(), "state": "idle", "phase": None},
    )
    manager._operation_lock.acquire()

    manager._recover_interrupted_update()

    status = manager.status()
    assert restored == [(old_commit, images)]
    assert status["state"] == "failed"
    assert status["message"] == "新版本复验失败，旧应用已恢复。"
    assert "已恢复旧应用" in str(status["error"])
    assert status["backup_path"] == "backup.dump"


def test_interrupted_recovery_preserves_the_database_backup_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    old_commit = git(worktree, "rev-parse", "HEAD")
    images = {
        "backend": {
            "image_name": "sage-data-manager-backend",
            "rollback_tag": "sage-data-manager-rollback-backend:operation-id",
        }
    }
    manager._replace_state(
        {
            **manager.status(),
            "state": "recovering",
            "phase": "interrupted_recovery",
            "interrupted_phase": "docker_build",
            "old_commit": old_commit,
            "target_commit": "f" * 40,
            "rollback_images": images,
            "backup_path": "backup.dump",
        }
    )
    monkeypatch.setattr(manager, "_rollback_application", lambda _commit, _rollback: None)
    monkeypatch.setattr(manager, "_cleanup_rollback_images", lambda _rollback: None)
    monkeypatch.setattr(
        manager,
        "_inspect_repository",
        lambda fetch: {**manager.status(), "state": "idle", "phase": None},
    )
    manager._operation_lock.acquire()

    manager._recover_interrupted_update()

    status = manager.status()
    assert status["state"] == "failed"
    assert status["message"] == "中断任务已处理，旧应用已恢复。"
    assert status["backup_path"] == "backup.dump"


def test_persisted_scheduled_backup_interruption_preserves_update_state(
    tmp_path: Path,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    config = agent_config(tmp_path, remote, worktree)
    config.state_directory.mkdir(parents=True)
    (config.state_directory / "status.json").write_text(
        json.dumps(
            {
                "state": "failed",
                "phase": "failed",
                "message": "更新失败",
                "error": "原更新错误",
                "backup_in_progress": True,
                "logs": [],
            }
        ),
        encoding="utf-8",
    )

    manager = sage_updater.UpdateManager(config)

    status = manager.status()
    assert status["state"] == "failed"
    assert status["error"] == "原更新错误"
    assert status["backup_in_progress"] is False
    assert status["last_backup_error"] == "上一次定时数据库备份被中断。"


@pytest.mark.parametrize("content", ["{", "[]", '{"state": "unknown"}'])
def test_invalid_persisted_state_fails_closed_without_overwriting_evidence(
    tmp_path: Path,
    content: str,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    config = agent_config(tmp_path, remote, worktree)
    config.state_directory.mkdir(parents=True)
    state_path = config.state_directory / "status.json"
    state_path.write_text(content, encoding="utf-8")

    manager = sage_updater.UpdateManager(config)

    status = manager.status()
    assert status["enabled"] is False
    assert status["state"] == "unavailable"
    assert status["phase"] == "state_load_failed"
    assert "status.json" in str(status["error"])
    assert state_path.read_text(encoding="utf-8") == content
    with pytest.raises(sage_updater.UpdateAgentError, match="status.json"):
        manager.check()
    manager.start_backup_scheduler()
    assert manager._backup_thread is None


def test_persisted_busy_state_starts_interrupted_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    config = agent_config(tmp_path, remote, worktree)
    config.state_directory.mkdir(parents=True)
    (config.state_directory / "status.json").write_text(
        json.dumps({"state": "building", "phase": "docker_build", "logs": []}),
        encoding="utf-8",
    )
    recovered = threading.Event()

    def record_recovery(manager) -> None:
        recovered.set()
        manager._operation_lock.release()

    monkeypatch.setattr(
        sage_updater.UpdateManager,
        "_recover_interrupted_update",
        record_recovery,
    )

    manager = sage_updater.UpdateManager(config)

    assert recovered.wait(timeout=1)
    assert manager.status()["state"] == "recovering"
    assert manager.status()["interrupted_phase"] == "docker_build"


def test_recovery_thread_start_failure_records_failure_and_releases_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    config = agent_config(tmp_path, remote, worktree)
    config.state_directory.mkdir(parents=True)
    (config.state_directory / "status.json").write_text(
        json.dumps(
            {
                "state": "building",
                "phase": "docker_build",
                "backup_path": "backup.dump",
                "logs": [],
            }
        ),
        encoding="utf-8",
    )

    class FailingThread:
        def __init__(self, **_kwargs: object) -> None:
            self.created = True

        def start(self) -> None:
            raise RuntimeError("thread capacity exhausted")

    monkeypatch.setattr(sage_updater.threading, "Thread", FailingThread)

    manager = sage_updater.UpdateManager(config)

    status = manager.status()
    assert status["state"] == "failed"
    assert status["backup_path"] == "backup.dump"
    assert "thread capacity exhausted" in str(status["error"])
    assert manager._operation_lock.acquire(blocking=False)
    manager._operation_lock.release()


def test_remote_url_normalization_accepts_https_and_ssh_forms() -> None:
    assert sage_updater.normalize_remote_url(
        "git@github.com:Zheng-Yu7463/SageDataManager.git"
    ) == sage_updater.normalize_remote_url("https://github.com/Zheng-Yu7463/SageDataManager/")


def test_postgres_environment_value_uses_running_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    commands: list[tuple[list[str], int]] = []

    def record_run(
        command: list[str], *, timeout: int, **_kwargs: object
    ) -> sage_updater.CommandResult:
        commands.append((command, timeout))
        return sage_updater.CommandResult(stdout=" sage user:@/#%= \r\n")

    monkeypatch.setattr(manager, "_run", record_run)

    value = manager._postgres_environment_value("POSTGRES_USER")

    assert value == " sage user:@/#%= "
    assert commands == [
        (
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "postgres",
                "printenv",
                "POSTGRES_USER",
            ],
            30,
        )
    ]


def test_postgres_environment_value_rejects_missing_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    monkeypatch.setattr(
        manager,
        "_run",
        lambda *_args, **_kwargs: sage_updater.CommandResult(stdout="\n"),
    )

    with pytest.raises(sage_updater.UpdateAgentError, match="缺少 POSTGRES_DB"):
        manager._postgres_environment_value("POSTGRES_DB")


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
    manager.check()
    status = wait_for_manager_state(manager, {"available", "failed"})

    assert status["update_available"] is True
    assert status["behind_count"] == 1
    assert status["commits"][0]["subject"] == "add update feature"


def test_check_rejects_a_dirty_worktree(tmp_path: Path) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    (worktree / "local-note.txt").write_text("do not overwrite\n", encoding="utf-8")

    manager.check()
    status = wait_for_manager_state(manager, {"failed"})

    assert "未提交" in str(status["error"])
    assert "?? local-note.txt" in str(status["error"])
    assert "git status --short" in str(status["error"])


def test_dirty_worktree_message_bounds_the_reported_changes() -> None:
    message = sage_updater.UpdateManager._dirty_worktree_message(
        "\n".join(f"?? local-{index}.txt" for index in range(7))
    )

    assert "?? local-0.txt" in message
    assert "?? local-4.txt" in message
    assert "local-5.txt" not in message
    assert "另有 2 项" in message


def test_check_returns_immediately_while_fetch_runs_in_background(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    original_inspect = manager._inspect_repository
    fetch_started = threading.Event()
    release_fetch = threading.Event()

    def delayed_inspect(*, fetch: bool):
        fetch_started.set()
        if not release_fetch.wait(timeout=2):
            raise AssertionError("测试未释放后台 fetch")
        return original_inspect(fetch=fetch)

    monkeypatch.setattr(manager, "_inspect_repository", delayed_inspect)
    started_at = time.monotonic()
    accepted = manager.check()

    try:
        assert time.monotonic() - started_at < 0.5
        assert accepted["state"] == "checking"
        assert fetch_started.wait(timeout=1)
        with pytest.raises(sage_updater.UpdateConflictError, match="正在运行"):
            manager.check()
    finally:
        release_fetch.set()

    status = wait_for_manager_state(manager, {"idle", "failed"})
    assert status["state"] == "idle"



def test_check_thread_start_failure_records_failure_and_releases_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))

    class FailingThread:
        def __init__(self, **_kwargs: object) -> None:
            self.created = True

        def start(self) -> None:
            raise RuntimeError("thread capacity exhausted")

    monkeypatch.setattr(sage_updater.threading, "Thread", FailingThread)

    with pytest.raises(sage_updater.UpdateAgentError, match="无法启动系统更新检查"):
        manager.check()

    status = manager.status()
    assert status["state"] == "failed"
    assert "thread capacity exhausted" in str(status["error"])
    assert manager._operation_lock.acquire(blocking=False)
    manager._operation_lock.release()

def test_start_update_reuses_the_remote_ref_fetched_by_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    current_commit = git(worktree, "rev-parse", "HEAD")
    target_commit = "f" * 40
    prepare_checked_update(manager, target_commit)
    fetch_values: list[bool] = []

    monkeypatch.setattr(
        manager,
        "_inspect_repository",
        lambda fetch: (
            fetch_values.append(fetch)
            or {
                **manager.status(),
                "current_commit": current_commit,
                "latest_commit": target_commit,
                "update_available": True,
                "worktree_clean": True,
            }
        ),
    )

    class DeferredThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self) -> None:
            manager._operation_lock.release()

    monkeypatch.setattr(sage_updater.threading, "Thread", DeferredThread)

    status = manager.start_update(target_commit)

    assert fetch_values == [False]
    assert status["state"] == "backing_up"
    assert status["latest_commit"] == target_commit



def test_update_thread_start_failure_records_failure_and_releases_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    current_commit = git(worktree, "rev-parse", "HEAD")
    target_commit = "f" * 40
    prepare_checked_update(manager, target_commit)
    monkeypatch.setattr(
        manager,
        "_inspect_repository",
        lambda fetch: {
            **manager.status(),
            "current_commit": current_commit,
            "latest_commit": target_commit,
            "update_available": True,
            "worktree_clean": True,
        },
    )

    class FailingThread:
        def __init__(self, **_kwargs: object) -> None:
            self.created = True

        def start(self) -> None:
            raise RuntimeError("thread capacity exhausted")

    monkeypatch.setattr(sage_updater.threading, "Thread", FailingThread)

    with pytest.raises(sage_updater.UpdateAgentError, match="无法启动系统更新任务"):
        manager.start_update(target_commit)

    status = manager.status()
    assert status["state"] == "failed"
    assert "thread capacity exhausted" in str(status["error"])
    assert manager._operation_lock.acquire(blocking=False)
    manager._operation_lock.release()

def test_start_update_rejects_a_commit_other_than_the_checked_target(tmp_path: Path) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    checked_commit = "f" * 40
    prepare_checked_update(manager, checked_commit)

    with pytest.raises(sage_updater.UpdateConflictError, match="最近检查结果"):
        manager.start_update("e" * 40)

    assert manager.status()["state"] == "available"
    assert manager.status()["latest_commit"] == checked_commit


def test_start_update_still_rejects_a_dirty_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    target_commit = "f" * 40
    prepare_checked_update(manager, target_commit)
    monkeypatch.setattr(
        manager,
        "_inspect_repository",
        lambda fetch: {
            **manager.status(),
            "worktree_clean": False,
            "worktree_changes": [" M compose.yaml"],
        },
    )

    with pytest.raises(sage_updater.UpdateAgentError, match="M compose.yaml"):
        manager.start_update(target_commit)

    assert manager.status()["state"] == "failed"


def test_start_update_records_preflight_errors_in_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    target_commit = "f" * 40
    prepare_checked_update(manager, target_commit)

    def reject_preflight(*, fetch: bool):
        raise sage_updater.UpdateAgentError("origin 地址不一致。")

    monkeypatch.setattr(manager, "_inspect_repository", reject_preflight)

    with pytest.raises(sage_updater.UpdateAgentError, match="origin"):
        manager.start_update(target_commit)

    status = manager.status()
    assert status["state"] == "failed"
    assert status["error"] == "origin 地址不一致。"


def test_start_update_keeps_already_current_status_out_of_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    target_commit = "f" * 40
    prepare_checked_update(manager, target_commit)
    monkeypatch.setattr(
        manager,
        "_inspect_repository",
        lambda fetch: {
            **manager.status(),
            "state": "idle",
            "message": "当前已经是最新版本。",
            "update_available": False,
            "worktree_clean": True,
        },
    )

    with pytest.raises(sage_updater.UpdateConflictError, match="最新版本"):
        manager.start_update(target_commit)

    assert manager.status()["state"] == "idle"


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


def test_protect_running_images_creates_stable_rollback_tags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    commands: list[list[str]] = []

    monkeypatch.setattr(
        manager,
        "_compose",
        lambda arguments, allow_empty=False: f"container-{arguments[-1]}",
    )

    def record_run(command: list[str], **kwargs):
        commands.append(command)
        service = command[-1].removeprefix("container-")
        if "{{.Image}}" in command:
            return sage_updater.CommandResult(stdout=f"sha256:{service}")
        if "{{.Config.Image}}" in command:
            return sage_updater.CommandResult(stdout=f"sage-data-manager-{service}")
        return sage_updater.CommandResult(stdout="")

    monkeypatch.setattr(manager, "_run", record_run)

    images = manager._protect_running_images("operation-id")

    assert images["backend"]["rollback_tag"] == ("sage-data-manager-rollback-backend:operation-id")
    assert [
        "docker",
        "image",
        "tag",
        "sha256:backend",
        "sage-data-manager-rollback-backend:operation-id",
    ] in commands
    assert manager.status()["rollback_images"] == images


def test_update_uses_the_commit_fetched_during_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    old_commit = git(worktree, "rev-parse", "HEAD")
    target_commit = "f" * 40
    commands: list[list[str]] = []

    monkeypatch.setattr(manager, "_protect_running_images", lambda operation_id: {})
    monkeypatch.setattr(manager, "_backup_database", lambda commit: tmp_path / "backup.dump")
    monkeypatch.setattr(manager, "_wait_for_health", lambda **kwargs: None)
    monkeypatch.setattr(manager, "_updater_files_changed", lambda: (False, False))
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
    manager._perform_update(old_commit, target_commit, "operation-id")

    assert manager.status()["last_backup_path"] == "backup.dump"
    assert ["git", "merge", "--ff-only", target_commit] in commands
    assert not any(command[:2] == ["git", "pull"] for command in commands)


def test_scheduled_backup_records_success_without_overwriting_update_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    backup_path = tmp_path / "scheduled.dump"
    manager._replace_state(
        {
            **manager.status(),
            "state": "failed",
            "phase": "failed",
            "message": "更新失败，旧应用已恢复。",
            "error": "镜像验证失败",
        }
    )
    pruned: list[Path] = []
    monkeypatch.setattr(manager, "_git", lambda arguments: "a" * 40)
    monkeypatch.setattr(
        manager,
        "_backup_database",
        lambda commit, **kwargs: backup_path,
    )
    monkeypatch.setattr(manager, "_prune_backups", pruned.append)

    assert manager._perform_scheduled_backup() is True

    status = manager.status()
    assert status["state"] == "failed"
    assert status["error"] == "镜像验证失败"
    assert status["last_backup_path"] == backup_path.name
    assert status["last_backup_at"]
    assert status["last_backup_error"] is None
    assert status["next_backup_at"]
    assert status["backup_in_progress"] is False
    assert pruned == [backup_path]
    assert manager._operation_lock.acquire(blocking=False)
    manager._operation_lock.release()


def test_scheduled_backup_retries_later_when_update_lock_is_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    backup_called = False

    def record_backup(commit: str, **kwargs) -> Path:
        nonlocal backup_called
        backup_called = True
        return tmp_path / "unexpected.dump"

    monkeypatch.setattr(manager, "_backup_database", record_backup)
    manager._operation_lock.acquire()
    try:
        assert manager._perform_scheduled_backup() is False
        assert backup_called is False
        assert manager.status()["next_backup_at"]
    finally:
        manager._operation_lock.release()


def test_scheduled_backup_failure_is_persisted_separately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    manager._replace_state(
        {
            **manager.status(),
            "state": "failed",
            "error": "原更新错误",
        }
    )
    monkeypatch.setattr(manager, "_git", lambda arguments: "b" * 40)

    def fail_backup(commit: str, **kwargs) -> Path:
        raise sage_updater.UpdateAgentError("测试备份失败")

    monkeypatch.setattr(manager, "_backup_database", fail_backup)

    assert manager._perform_scheduled_backup() is False

    status = manager.status()
    assert status["state"] == "failed"
    assert status["error"] == "原更新错误"
    assert status["last_backup_error"] == "测试备份失败"
    assert status["last_backup_at"] is None
    assert status["backup_in_progress"] is False


def test_backup_retention_keeps_current_and_newest_backup(tmp_path: Path) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    config = replace(
        agent_config(tmp_path, remote, worktree),
        backup_retention_count=2,
    )
    manager = sage_updater.UpdateManager(config)
    backup_directory = config.state_directory / "backups"
    backup_directory.mkdir(parents=True)
    backups = [backup_directory / f"sage-{index}.dump" for index in range(3)]
    for index, path in enumerate(backups, start=1):
        path.touch()
        os.utime(path, (index, index))

    manager._prune_backups(backups[0])

    assert backups[0].is_file()
    assert not backups[1].exists()
    assert backups[2].is_file()


def test_backup_scheduler_stops_waiting_thread_cleanly(tmp_path: Path) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    config = replace(
        agent_config(tmp_path, remote, worktree),
        scheduled_backup_interval_seconds=3600,
    )
    manager = sage_updater.UpdateManager(config)

    manager.start_backup_scheduler()
    thread = manager._backup_thread
    assert thread is not None
    assert thread.is_alive()
    assert manager.status()["next_backup_at"]

    manager.stop_backup_scheduler()

    assert not thread.is_alive()
    assert manager._backup_thread is None


def test_disabled_backup_scheduler_has_no_thread(tmp_path: Path) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    config = replace(
        agent_config(tmp_path, remote, worktree),
        scheduled_backup_interval_seconds=0,
    )
    manager = sage_updater.UpdateManager(config)

    manager.start_backup_scheduler()

    assert manager._backup_thread is None
    assert manager.status()["next_backup_at"] is None


def test_backup_scheduler_thread_start_failure_clears_thread_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))

    class FailingThread:
        def __init__(self, **_kwargs: object) -> None:
            self.created = True

        def start(self) -> None:
            raise RuntimeError("thread capacity exhausted")

    monkeypatch.setattr(sage_updater.threading, "Thread", FailingThread)

    with pytest.raises(sage_updater.UpdateAgentError, match="定时数据库备份"):
        manager.start_backup_scheduler()

    status = manager.status()
    assert manager._backup_thread is None
    assert "thread capacity exhausted" in str(status["last_backup_error"])


def test_run_reports_multiple_stderr_lines(tmp_path: Path) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))

    with pytest.raises(sage_updater.UpdateAgentError) as captured:
        manager._run(
            ["sh", "-c", "printf 'first detail\\nsecond detail\\n' >&2; exit 1"],
            timeout=10,
        )

    assert "first detail；second detail" in str(captured.value)


def test_run_terminates_the_process_group_after_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    communicate_calls: list[int | None] = []
    kill_calls: list[tuple[int, int]] = []
    popen_options: dict[str, object] = {}

    class TimedOutProcess:
        pid = 4321
        returncode = -15

        def communicate(self, timeout: int | None = None) -> tuple[str, str]:
            communicate_calls.append(timeout)
            if len(communicate_calls) == 1:
                raise subprocess.TimeoutExpired(["test-command"], timeout)
            return "", ""

    def fake_popen(_command: list[str], **kwargs: object) -> TimedOutProcess:
        popen_options.update(kwargs)
        return TimedOutProcess()

    monkeypatch.setattr(sage_updater.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        sage_updater.os,
        "killpg",
        lambda process_id, signum: kill_calls.append((process_id, signum)),
    )

    with pytest.raises(sage_updater.UpdateAgentError, match="执行超时（1 秒）"):
        manager._run(["test-command", "argument"], timeout=1)

    assert popen_options["start_new_session"] is True
    assert communicate_calls == [1, 5]
    assert kill_calls == [(4321, sage_updater.signal.SIGTERM)]


def test_persist_state_flushes_file_and_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, worktree = create_repository_pair(tmp_path)
    manager = sage_updater.UpdateManager(agent_config(tmp_path, remote, worktree))
    status_path = manager.config.state_directory / "status.json"
    real_fsync = sage_updater.os.fsync
    fsync_calls: list[int] = []

    def record_fsync(file_descriptor: int) -> None:
        fsync_calls.append(file_descriptor)
        real_fsync(file_descriptor)

    monkeypatch.setattr(sage_updater.os, "fsync", record_fsync)
    manager._set_value("message", "durable state")

    assert json.loads(status_path.read_text(encoding="utf-8"))["message"] == "durable state"
    assert status_path.stat().st_mode & 0o777 == 0o600
    assert not status_path.with_suffix(".tmp").exists()
    assert len(fsync_calls) == 2


def test_default_readiness_url_uses_frontend_proxy() -> None:
    repository = MODULE_PATH.parents[1]
    config = sage_updater.AgentConfig(
        repository=repository,
        socket_path=Path("/run/sage-updater/updater.sock"),
        state_directory=Path("/var/lib/sage-updater"),
        secret="s" * 64,
    )

    assert config.backend_health_url == sage_updater.urljoin(
        config.frontend_health_url,
        "api/ready",
    )


def test_installer_keeps_the_socket_mount_visible_after_agent_restart() -> None:
    repository = MODULE_PATH.parents[1]
    service = (repository / "deploy/sage-updater.service").read_text(encoding="utf-8")
    installer = (repository / "deploy/install-updater.sh").read_text(encoding="utf-8")

    assert "RuntimeDirectoryPreserve=yes" in service
    assert "docker compose config --format json" in installer
    assert '["services"]["backend"]["environment"]' in installer
    assert "docker compose up --build -d --force-recreate --no-deps backend" in installer
