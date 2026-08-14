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
