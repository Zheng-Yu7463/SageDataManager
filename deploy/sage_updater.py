#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer
from typing import BinaryIO, NoReturn
from urllib.parse import urljoin

BUSY_STATES = {
    "checking", "recovering", "backing_up", "pulling", "building", "restarting", "verifying"
}
EXPECTED_REMOTE = "https://github.com/Zheng-Yu7463/SageDataManager"
UTC_TIMEZONE = timezone.utc  # noqa: UP017 -- host Python 3.10 lacks datetime.UTC.
SNAP_DOCKER_BINARY = Path("/snap/docker/current/bin/docker")
SNAP_COMPOSE_BINARY = Path("/snap/docker/current/usr/libexec/docker/cli-plugins/docker-compose")


class UpdateAgentError(Exception):
    pass


class UpdateConflictError(UpdateAgentError):
    pass


@dataclass(frozen=True)
class AgentConfig:
    repository: Path
    socket_path: Path
    state_directory: Path
    secret: str
    branch: str = "main"
    remote: str = "origin"
    expected_remote: str = EXPECTED_REMOTE
    backend_health_url: str = "http://127.0.0.1:8000/api/ready"
    frontend_health_url: str = "http://127.0.0.1:8080/"
    backup_retention_count: int = 10
    docker_command: tuple[str, ...] = ("docker",)
    compose_command: tuple[str, ...] = ("docker", "compose")


@dataclass(frozen=True)
class CommandResult:
    stdout: str

class FrontendAssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.script_sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        source = dict(attrs).get("src")
        if source:
            self.script_sources.append(source)




def utc_now() -> str:
    return datetime.now(UTC_TIMEZONE).isoformat()


def normalize_remote_url(value: str) -> str:
    normalized = value.strip().removesuffix("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    if normalized.startswith("git@github.com:"):
        normalized = "https://github.com/" + normalized.removeprefix("git@github.com:")
    return normalized.lower()


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def resolve_container_commands(
    docker_executable: str | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    executable = docker_executable or shutil.which("docker") or "docker"
    if (
        executable == "/snap/bin/docker"
        and SNAP_DOCKER_BINARY.is_file()
        and SNAP_COMPOSE_BINARY.is_file()
    ):
        return (str(SNAP_DOCKER_BINARY),), (str(SNAP_COMPOSE_BINARY),)
    return (executable,), (executable, "compose")


class UpdateManager:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._state: dict[str, object] = {
            "enabled": True,
            "state": "idle",
            "phase": None,
            "message": "更新服务已就绪。",
            "branch": config.branch,
            "current_commit": None,
            "latest_commit": None,
            "checked_at": None,
            "update_available": False,
            "behind_count": 0,
            "ahead_count": 0,
            "worktree_clean": None,
            "remote_url": None,
            "commits": [],
            "started_at": None,
            "completed_at": None,
            "error": None,
            "backup_path": None,
            "operation_id": None,
            "agent_restart_required": False,
            "installer_restart_required": False,
            "old_commit": None,
            "target_commit": None,
            "rollback_images": {},
            "updater_files": {},
            "logs": [],
        }
        persisted = self._load_state()
        if persisted:
            self._state.update(persisted)
            if self._state.get("state") in BUSY_STATES:
                self._state.update(
                    {
                        "interrupted_phase": self._state.get("phase"),
                        "state": "recovering",
                        "phase": "interrupted_recovery",
                        "message": "检测到未完成的更新，正在恢复旧应用。",
                    }
                )
                self._persist_state()
                self._operation_lock.acquire()
                threading.Thread(
                    target=self._recover_interrupted_update,
                    name="sage-system-update-recovery",
                    daemon=False,
                ).start()
                return
            if self._state.get("state") in {"succeeded", "failed"}:
                if self._state.get("agent_restart_required"):
                    self._state["agent_restart_required"] = False
                    self._state["message"] = "系统更新完成，更新代理已重新加载。"
                    self._persist_state()
                return

        try:
            self._replace_state(self._inspect_repository(fetch=False))
        except UpdateAgentError as error:
            self._set_failure(str(error))

    def status(self) -> dict[str, object]:
        with self._lock:
            return json.loads(json.dumps(self._state))

    def _recover_interrupted_update(self) -> None:
        snapshot = self.status()
        previous_phase = str(
            snapshot.get("interrupted_phase") or snapshot.get("phase") or "unknown"
        )
        old_commit = snapshot.get("old_commit")
        rollback_images = snapshot.get("rollback_images")
        try:
            if previous_phase == "committed":
                target_commit = snapshot.get("target_commit")
                if not isinstance(target_commit, str):
                    raise UpdateAgentError("中断事务缺少目标 Commit。")
                try:
                    self._wait_for_health(expected_commit=target_commit)
                except Exception as verification_error:
                    if not (
                        isinstance(old_commit, str)
                        and isinstance(rollback_images, dict)
                        and rollback_images
                    ):
                        raise
                    self._append_log("新版本复验失败，正在恢复旧应用。")
                    self._rollback_application(old_commit, rollback_images)
                    self._cleanup_rollback_images(rollback_images)
                    inspected = self._inspect_repository(fetch=False)
                    self._replace_state(inspected)
                    self._set_failure(
                        f"新版本复验失败：{verification_error}；已恢复旧应用。"
                        "数据库迁移不会自动降级。",
                        message="新版本复验失败，旧应用已恢复。",
                        backup_path=snapshot.get("backup_path")
                        if isinstance(snapshot.get("backup_path"), str)
                        else None,
                    )
                    return
                _, installer_changed = self._updater_files_changed()
                inspected = self._inspect_repository(fetch=False)
                self._replace_state(
                    {
                        **inspected,
                        "state": "succeeded",
                        "phase": "complete",
                        "message": "更新已完成；代理重启后已确认运行状态。",
                        "started_at": snapshot.get("started_at"),
                        "completed_at": utc_now(),
                        "backup_path": snapshot.get("backup_path"),
                        "operation_id": snapshot.get("operation_id"),
                        "installer_restart_required": installer_changed,
                        "logs": snapshot.get("logs", []),
                    }
                )
                if isinstance(rollback_images, dict):
                    try:
                        self._cleanup_rollback_images(rollback_images)
                    except Exception as housekeeping_error:
                        self._append_log(f"无法清理回滚镜像：{housekeeping_error}")
                return

            restored = (
                isinstance(old_commit, str)
                and isinstance(rollback_images, dict)
                and bool(rollback_images)
            )
            if restored:
                self._append_log(f"从中断阶段 {previous_phase} 恢复旧应用。")
                self._rollback_application(old_commit, rollback_images)
                self._cleanup_rollback_images(rollback_images)
            inspected = self._inspect_repository(fetch=False)
            self._replace_state(inspected)
            self._set_failure(
                "上一次更新进程被中断，已停止继续更新。数据库迁移不会自动降级。",
                message="中断任务已处理，旧应用已恢复。"
                if restored
                else "中断的检查任务已停止，请重新检查更新。",
            )
        except Exception as error:
            self._set_failure(
                f"中断恢复失败：{error}",
                message="检测到未完成更新，自动恢复失败，请在服务器人工处理。",
            )
        finally:
            self._operation_lock.release()


    def check(self) -> dict[str, object]:
        if not self._operation_lock.acquire(blocking=False):
            raise UpdateConflictError("已有系统更新操作正在运行。")
        try:
            self._set_progress("checking", "fetch", "正在连接 GitHub 获取 origin/main…", reset=True)
            thread = threading.Thread(
                target=self._perform_check,
                name="sage-system-update-check",
                daemon=False,
            )
            thread.start()
            return self.status()
        except Exception:
            self._operation_lock.release()
            raise

    def _perform_check(self) -> None:
        try:
            inspected = self._inspect_repository(fetch=True)
            self._replace_state(inspected)
        except UpdateAgentError as error:
            self._set_failure(str(error))
        except Exception as error:
            self._set_failure(f"检查更新失败：{error}")
        finally:
            self._operation_lock.release()

    def start_update(self, expected_commit: str) -> dict[str, object]:
        if not self._operation_lock.acquire(blocking=False):
            raise UpdateConflictError("已有系统更新操作正在运行。")
        try:
            with self._lock:
                checked_commit = self._state.get("latest_commit")
                checked_at = self._state.get("checked_at")
                checked_state = self._state.get("state")
            if len(expected_commit) != 40 or any(
                character not in "0123456789abcdef" for character in expected_commit
            ):
                raise UpdateConflictError("目标 Commit 格式无效。")
            if checked_state != "available" or not checked_at:
                raise UpdateConflictError("请先重新检查更新，再确认目标 Commit。")
            if checked_commit != expected_commit:
                raise UpdateConflictError("目标 Commit 与最近检查结果不一致，请重新检查更新。")

            self._set_progress("checking", "preflight", "正在校验已检查的 Commit…", reset=True)
            inspected = self._inspect_repository(fetch=False)
            if not inspected["worktree_clean"]:
                raise UpdateAgentError("Git 工作区存在未提交内容，拒绝自动更新。")
            if inspected["latest_commit"] != expected_commit:
                raise UpdateConflictError("origin/main 已发生变化，请重新检查并确认更新。")
            if not inspected["update_available"]:
                self._replace_state(inspected)
                raise UpdateConflictError("当前已经是 origin/main 的最新版本。")

            old_commit = str(inspected["current_commit"])
            operation_id = uuid.uuid4().hex
            self._replace_state(
                {
                    **inspected,
                    "state": "backing_up",
                    "phase": "protecting_images",
                    "message": "更新任务已接受，正在保护当前应用镜像。",
                    "started_at": utc_now(),
                    "completed_at": None,
                    "error": None,
                    "backup_path": None,
                    "operation_id": operation_id,
                    "old_commit": old_commit,
                    "target_commit": expected_commit,
                    "rollback_images": {},
                    "updater_files": self._capture_updater_files(),
                }
            )
            thread = threading.Thread(
                target=self._perform_update,
                args=(old_commit, expected_commit, operation_id),
                name="sage-system-update",
                daemon=False,
            )
            thread.start()
            return self.status()
        except UpdateConflictError:
            self._operation_lock.release()
            raise
        except UpdateAgentError as error:
            self._set_failure(str(error))
            self._operation_lock.release()
            raise
        except Exception:
            self._operation_lock.release()
            raise

    def _perform_update(
        self,
        old_commit: str,
        target_commit: str,
        operation_id: str,
    ) -> None:
        backup_path: Path | None = None
        rollback_images: dict[str, dict[str, str]] = {}
        restart_agent = False
        try:
            rollback_images = self._protect_running_images(operation_id)
            self._set_value("rollback_images", rollback_images)

            self._set_progress(
                "backing_up",
                "database_backup",
                "当前应用镜像已保护，正在备份 PostgreSQL。",
            )
            backup_path = self._backup_database(old_commit)
            self._set_value("backup_path", backup_path.name)
            try:
                self._prune_backups(backup_path)
            except Exception as housekeeping_error:
                self._append_log(f"无法清理过期数据库备份：{housekeeping_error}")

            self._set_progress("pulling", "git_merge", "正在 fast-forward 到已检查的 Commit…")
            self._run(["git", "merge", "--ff-only", target_commit], timeout=120)
            actual_commit = self._git(["rev-parse", "HEAD"])
            if actual_commit != target_commit:
                raise UpdateAgentError("拉取完成后的 Commit 与检查结果不一致，已停止更新。")

            release_environment = {"SAGE_RELEASE_COMMIT": target_commit}
            self._set_progress("building", "docker_build", "正在构建后端与前端镜像…")
            self._run(
                [*self.config.compose_command, "build", "backend", "frontend"],
                timeout=2400,
                extra_env=release_environment,
            )

            self._set_progress("restarting", "compose_up", "正在重建应用容器并运行迁移…")
            self._run(
                [*self.config.compose_command, "up", "-d", "backend", "frontend"],
                timeout=600,
                extra_env=release_environment,
            )

            self._set_progress("verifying", "health_check", "正在验证数据库、前端和运行版本…")
            self._wait_for_health(expected_commit=target_commit)
            self._set_value("phase", "committed")

            agent_changed, installer_changed = self._updater_files_changed()
            inspected = self._inspect_repository(fetch=False)
            self._replace_state(
                {
                    **inspected,
                    "state": "succeeded",
                    "phase": "complete",
                    "message": "系统已更新，数据库、前端和运行版本验证通过。",
                    "started_at": self.status().get("started_at"),
                    "completed_at": utc_now(),
                    "error": None,
                    "backup_path": backup_path.name,
                    "operation_id": operation_id,
                    "agent_restart_required": agent_changed,
                    "installer_restart_required": installer_changed,
                    "logs": self.status().get("logs", []),
                }
            )
            try:
                self._cleanup_rollback_images(rollback_images)
            except Exception as housekeeping_error:
                self._append_log(f"无法清理回滚镜像：{housekeeping_error}")
            restart_agent = agent_changed
        except Exception as error:
            original_error = str(error)
            rollback_error: str | None = None
            try:
                self._append_log("更新失败，正在恢复旧 Commit 和受保护的应用镜像。")
                self._rollback_application(old_commit, rollback_images)
                self._cleanup_rollback_images(rollback_images)
            except Exception as restore_error:
                rollback_error = str(restore_error)
            message = "更新失败，旧应用已恢复；数据库迁移未自动降级。"
            if rollback_error:
                message = "更新失败，自动恢复也未完整成功，请在服务器上人工处理。"
            detail = original_error
            if rollback_error:
                detail += f"；恢复错误：{rollback_error}"
            self._set_failure(
                detail,
                message=message,
                backup_path=backup_path.name if backup_path else None,
            )
        finally:
            self._operation_lock.release()
            if restart_agent:
                self._schedule_agent_restart()

    def _inspect_repository(self, *, fetch: bool) -> dict[str, object]:
        repository = self.config.repository
        if not (repository / ".git").exists():
            raise UpdateAgentError(f"更新目录不是 Git 仓库：{repository}")

        branch = self._git(["branch", "--show-current"])
        if branch != self.config.branch:
            raise UpdateAgentError(
                f"服务器必须位于 {self.config.branch} 分支，当前为 {branch or 'detached'}。"
            )

        remote_url = self._git(["remote", "get-url", self.config.remote])
        if normalize_remote_url(remote_url) != normalize_remote_url(self.config.expected_remote):
            raise UpdateAgentError("origin 地址与允许更新的 SageDataManager 仓库不一致。")

        worktree_output = self._git(["status", "--porcelain"])
        worktree_clean = not worktree_output
        if fetch:
            if not worktree_clean:
                raise UpdateAgentError("Git 工作区存在未提交内容，拒绝自动更新。")
            self._run(
                ["git", "fetch", "--quiet", self.config.remote, self.config.branch],
                timeout=180,
            )

        current_commit = self._git(["rev-parse", "HEAD"])
        remote_ref = f"{self.config.remote}/{self.config.branch}"
        latest_commit = self._git(["rev-parse", remote_ref])
        counts = self._git(["rev-list", "--left-right", "--count", f"HEAD...{remote_ref}"])
        try:
            ahead_count, behind_count = (int(value) for value in counts.split())
        except ValueError as error:
            raise UpdateAgentError("无法解析 Git 分支差异。") from error
        if ahead_count:
            raise UpdateAgentError("本地 main 含远端不存在的提交，拒绝自动更新。")

        commits: list[dict[str, str]] = []
        if behind_count:
            log_output = self._git(
                [
                    "log",
                    "--format=%H%x1f%h%x1f%s%x1f%an%x1f%cI",
                    "--max-count=20",
                    f"HEAD..{remote_ref}",
                ]
            )
            for line in log_output.splitlines():
                parts = line.split("\x1f")
                if len(parts) == 5:
                    commits.append(
                        {
                            "sha": parts[0],
                            "short_sha": parts[1],
                            "subject": parts[2],
                            "author": parts[3],
                            "committed_at": parts[4],
                        }
                    )

        return {
            "enabled": True,
            "state": "available" if behind_count else "idle",
            "phase": None,
            "message": f"发现 {behind_count} 个可用提交。"
            if behind_count
            else "当前已经是最新版本。",
            "branch": branch,
            "current_commit": current_commit,
            "latest_commit": latest_commit,
            "checked_at": utc_now() if fetch else self.status().get("checked_at"),
            "update_available": behind_count > 0,
            "behind_count": behind_count,
            "ahead_count": ahead_count,
            "worktree_clean": worktree_clean,
            "remote_url": remote_url,
            "commits": commits,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "backup_path": None,
            "operation_id": None,
            "agent_restart_required": False,
            "installer_restart_required": False,
            "rollback_images": {},
            "logs": self.status().get("logs", []),
        }

    def _backup_database(self, commit: str) -> Path:
        self.config.state_directory.mkdir(parents=True, exist_ok=True)
        backup_directory = self.config.state_directory / "backups"
        backup_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        timestamp = datetime.now(UTC_TIMEZONE).strftime("%Y%m%dT%H%M%SZ")
        operation_suffix = str(self.status().get("operation_id") or "manual")[:8]
        backup_path = backup_directory / f"sage-{timestamp}-{commit[:12]}-{operation_suffix}.dump"
        partial_path = backup_path.with_suffix(".dump.partial")
        env_values = read_dotenv(self.config.repository / ".env")
        database_user = env_values.get("POSTGRES_USER", "sage")
        database_name = env_values.get("POSTGRES_DB", "sage")

        size_result = self._run(
            [
                *self.config.compose_command,
                "exec",
                "-T",
                "postgres",
                "psql",
                "-At",
                "-U",
                database_user,
                "-d",
                database_name,
                "-c",
                "SELECT pg_database_size(current_database())",
            ],
            timeout=60,
        ).stdout.strip()
        try:
            database_size = int(size_result)
        except ValueError as error:
            raise UpdateAgentError("无法读取 PostgreSQL 数据库容量。") from error
        required_free = max(database_size * 2, 512 * 1024 * 1024)
        if shutil.disk_usage(backup_directory).free < required_free:
            raise UpdateAgentError("备份目录可用空间不足，拒绝开始更新。")

        self._append_log(f"创建并校验数据库备份 {backup_path.name}")
        try:
            with partial_path.open("wb") as output:
                self._run(
                    [
                        *self.config.compose_command,
                        "exec",
                        "-T",
                        "postgres",
                        "pg_dump",
                        "--format=custom",
                        "--no-owner",
                        "--no-acl",
                        "-U",
                        database_user,
                        "-d",
                        database_name,
                    ],
                    timeout=600,
                    stdout=output,
                )
            if partial_path.stat().st_size == 0:
                raise UpdateAgentError("PostgreSQL 备份为空，拒绝继续更新。")
            with partial_path.open("rb") as backup_input:
                self._run(
                    [
                        *self.config.compose_command,
                        "exec",
                        "-T",
                        "postgres",
                        "pg_restore",
                        "--list",
                    ],
                    timeout=120,
                    stdin=backup_input,
                )
            partial_path.chmod(0o600)
            partial_path.replace(backup_path)
        except Exception:
            partial_path.unlink(missing_ok=True)
            raise
        return backup_path

    def _prune_backups(self, current_backup: Path) -> None:
        backups = sorted(
            current_backup.parent.glob("sage-*.dump"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        keep_count = max(1, self.config.backup_retention_count)
        retained_paths = set(backups[:keep_count])
        retained_paths.add(current_backup)
        if len(retained_paths) > keep_count:
            oldest_other = min(
                (path for path in retained_paths if path != current_backup),
                key=lambda path: path.stat().st_mtime,
            )
            retained_paths.remove(oldest_other)
        for path in backups:
            if path in retained_paths:
                continue
            path.unlink(missing_ok=True)
            self._append_log(f"清理过期数据库备份 {path.name}")

    def _protect_running_images(
        self,
        operation_id: str,
    ) -> dict[str, dict[str, str]]:
        images: dict[str, dict[str, str]] = {}
        try:
            for service in ("backend", "frontend"):
                container_id = self._compose(["ps", "-q", service], allow_empty=True)
                if not container_id:
                    raise UpdateAgentError(f"{service} 容器未运行，无法创建可靠回滚点。")
                image_id = self._run(
                    [
                        *self.config.docker_command,
                        "inspect",
                        "--format",
                        "{{.Image}}",
                        container_id,
                    ],
                    timeout=30,
                ).stdout.strip()
                image_name = self._run(
                    [
                        *self.config.docker_command,
                        "inspect",
                        "--format",
                        "{{.Config.Image}}",
                        container_id,
                    ],
                    timeout=30,
                ).stdout.strip()
                if not image_id or not image_name:
                    raise UpdateAgentError(f"无法读取 {service} 当前运行镜像。")
                self._run(
                    [*self.config.docker_command, "image", "inspect", image_id],
                    timeout=30,
                )
                rollback_tag = f"sage-data-manager-rollback-{service}:{operation_id}"
                self._run(
                    [*self.config.docker_command, "image", "tag", image_id, rollback_tag],
                    timeout=60,
                )
                images[service] = {
                    "image_id": image_id,
                    "image_name": image_name,
                    "rollback_tag": rollback_tag,
                }
                self._set_value("rollback_images", images)
                self._append_log(f"已保护 {service} 镜像：{rollback_tag}")
        except Exception:
            self._cleanup_rollback_images(images)
            raise
        return images

    def _cleanup_rollback_images(self, images: dict[str, dict[str, str]]) -> None:
        for image in images.values():
            rollback_tag = image.get("rollback_tag")
            if not rollback_tag:
                continue
            try:
                self._run(
                    [*self.config.docker_command, "image", "rm", rollback_tag],
                    timeout=60,
                )
            except UpdateAgentError as error:
                self._append_log(f"无法清理回滚镜像 {rollback_tag}：{error}")

    def _capture_updater_files(self) -> dict[str, str]:
        paths = {
            "agent": self.config.repository / "deploy/sage_updater.py",
            "service": self.config.repository / "deploy/sage-updater.service",
            "installer": self.config.repository / "deploy/install-updater.sh",
        }
        return {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in paths.items()
            if path.is_file()
        }

    def _updater_files_changed(self) -> tuple[bool, bool]:
        previous = self.status().get("updater_files")
        if not isinstance(previous, dict):
            return False, False
        current = self._capture_updater_files()
        agent_changed = previous.get("agent") != current.get("agent")
        installer_changed = any(
            previous.get(name) != current.get(name) for name in ("service", "installer")
        )
        return agent_changed, installer_changed

    def _schedule_agent_restart(self) -> None:
        self._append_log("更新代理代码已变化，将由 systemd 自动重新加载。")

        def restart() -> None:
            os.kill(os.getpid(), signal.SIGTERM)

        threading.Timer(2, restart).start()

    def _rollback_application(
        self,
        old_commit: str,
        old_images: dict[str, dict[str, str]],
    ) -> None:
        current_commit = self._git(["rev-parse", "HEAD"])
        if current_commit != old_commit:
            self._run(["git", "reset", "--hard", old_commit], timeout=120)
        for image in old_images.values():
            rollback_tag = image.get("rollback_tag")
            image_name = image.get("image_name")
            if not rollback_tag or not image_name:
                raise UpdateAgentError("回滚镜像记录不完整。")
            self._run(
                [*self.config.docker_command, "image", "inspect", rollback_tag],
                timeout=30,
            )
            self._run(
                [*self.config.docker_command, "image", "tag", rollback_tag, image_name],
                timeout=60,
            )
        if old_images:
            self._run(
                [
                    *self.config.compose_command,
                    "up",
                    "-d",
                    "--no-build",
                    "--force-recreate",
                    "backend",
                    "frontend",
                ],
                timeout=600,
                extra_env={"SAGE_RELEASE_COMMIT": old_commit},
            )
            self._wait_for_health(expected_commit=old_commit)

    def _wait_for_health(self, *, expected_commit: str) -> None:
        deadline = time.monotonic() + 240
        last_error = "服务尚未响应"
        while time.monotonic() < deadline:
            try:
                self._check_backend_readiness(expected_commit)
                self._check_frontend_assets()
                self._verify_running_release(expected_commit)
                return
            except (OSError, ValueError, UpdateAgentError, urllib.error.URLError) as error:
                last_error = str(error)
            time.sleep(3)
        raise UpdateAgentError(f"应用健康检查超时：{last_error}")

    def _check_backend_readiness(self, expected_commit: str) -> None:
        with urllib.request.urlopen(self.config.backend_health_url, timeout=5) as response:
            if response.status != 200:
                raise UpdateAgentError(
                    f"{self.config.backend_health_url} 返回 HTTP {response.status}"
                )
            payload = json.loads(response.read(1_000_001))
        if payload.get("status") != "ready":
            raise UpdateAgentError("后端 readiness 响应无效。")
        if payload.get("release_commit") != expected_commit:
            raise UpdateAgentError("后端运行 Commit 与目标 Commit 不一致。")
        if not payload.get("database_revision"):
            raise UpdateAgentError("后端未返回数据库迁移版本。")

    def _check_frontend_assets(self) -> None:
        with urllib.request.urlopen(self.config.frontend_health_url, timeout=5) as response:
            if response.status != 200:
                raise UpdateAgentError(
                    f"{self.config.frontend_health_url} 返回 HTTP {response.status}"
                )
            html = response.read(1_000_001).decode("utf-8")
        if 'id="app"' not in html:
            raise UpdateAgentError("前端入口页面缺少应用挂载点。")
        parser = FrontendAssetParser()
        parser.feed(html)
        if not parser.script_sources:
            raise UpdateAgentError("前端入口页面没有可加载的脚本资源。")
        asset_url = urljoin(self.config.frontend_health_url, parser.script_sources[0])
        with urllib.request.urlopen(asset_url, timeout=5) as response:
            if response.status != 200 or not response.read(1):
                raise UpdateAgentError("前端脚本资源不可用。")

    def _verify_running_release(self, expected_commit: str) -> None:
        for service in ("backend", "frontend"):
            container_id = self._compose(["ps", "-q", service], allow_empty=True)
            if not container_id:
                raise UpdateAgentError(f"{service} 容器未运行。")
            release = self._run(
                [
                    *self.config.docker_command,
                    "inspect",
                    "--format",
                    '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
                    container_id,
                ],
                timeout=30,
            ).stdout.strip()
            if release != expected_commit:
                raise UpdateAgentError(f"{service} 镜像 Commit 与目标 Commit 不一致。")

    def _compose(self, arguments: list[str], *, allow_empty: bool = False) -> str:
        result = self._run([*self.config.compose_command, *arguments], timeout=60)
        output = result.stdout.strip()
        if not output and not allow_empty:
            raise UpdateAgentError("Docker Compose 没有返回预期结果。")
        return output

    def _git(self, arguments: list[str]) -> str:
        return self._run(["git", *arguments], timeout=60).stdout.strip()

    def _run(
        self,
        command: list[str],
        *,
        timeout: int,
        stdout: BinaryIO | None = None,
        stdin: BinaryIO | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> CommandResult:
        environment = os.environ.copy()
        if extra_env:
            environment.update(extra_env)
        try:
            completed = subprocess.run(
                command,
                cwd=self.config.repository,
                check=False,
                stdin=stdin,
                stdout=stdout or subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=stdout is None and stdin is None,
                timeout=timeout,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise UpdateAgentError(f"命令执行失败：{command[0]} {command[1]}") from error

        standard_output = completed.stdout if isinstance(completed.stdout, str) else ""
        standard_error = (
            completed.stderr.decode(errors="replace")
            if isinstance(completed.stderr, bytes)
            else completed.stderr
        )
        if completed.returncode != 0:
            detail = (standard_error or standard_output or "未知错误").strip().splitlines()
            summary = "；".join(line.strip() for line in detail[-4:] if line.strip())
            if not summary:
                summary = "未知错误"
            raise UpdateAgentError(f"{command[0]} {command[1]} 失败：{summary}")
        if stdout is None:
            lines = [
                line.strip()
                for line in (standard_output + "\n" + (standard_error or "")).splitlines()
                if line.strip()
            ]
            for line in lines[-4:]:
                self._append_log(line[:500])
        return CommandResult(stdout=standard_output)

    def _replace_state(self, values: dict[str, object]) -> None:
        with self._lock:
            self._state = values
            self._persist_state()

    def _set_value(self, key: str, value: object) -> None:
        with self._lock:
            self._state[key] = value
            self._persist_state()

    def _set_progress(
        self,
        state: str,
        phase: str,
        message: str,
        *,
        reset: bool = False,
    ) -> None:
        with self._lock:
            self._state.update(
                {
                    "state": state,
                    "phase": phase,
                    "message": message,
                    "error": None,
                }
            )
            if reset:
                self._state.update(
                    {
                        "started_at": None,
                        "completed_at": None,
                        "backup_path": None,
                        "logs": [],
                    }
                )
            self._append_log(message, persist=False)
            self._persist_state()

    def _set_failure(
        self,
        detail: str,
        *,
        message: str = "系统更新操作失败。",
        backup_path: str | None = None,
    ) -> None:
        with self._lock:
            self._state.update(
                {
                    "enabled": True,
                    "state": "failed",
                    "phase": "failed",
                    "message": message,
                    "completed_at": utc_now(),
                    "error": detail,
                    "backup_path": backup_path,
                }
            )
            self._append_log(detail, persist=False)
            self._persist_state()

    def _append_log(self, message: str, *, persist: bool = True) -> None:
        with self._lock:
            logs = list(self._state.get("logs", []))
            logs.append(f"{datetime.now(UTC_TIMEZONE).strftime('%H:%M:%S')} {message}")
            self._state["logs"] = logs[-40:]
            if persist:
                self._persist_state()

    def _load_state(self) -> dict[str, object] | None:
        path = self.config.state_directory / "status.json"
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict):
            return None
        return value

    def _persist_state(self) -> None:
        self.config.state_directory.mkdir(parents=True, exist_ok=True)
        path = self.config.state_directory / "status.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


class AgentHTTPServer(ThreadingMixIn, UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, socket_path: Path, manager: UpdateManager, secret: str) -> None:
        self.manager = manager
        self.secret = secret
        super().__init__(str(socket_path), AgentRequestHandler)


class AgentRequestHandler(BaseHTTPRequestHandler):
    server: AgentHTTPServer

    def address_string(self) -> str:
        return "local"

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[sage-updater] {format_string % args}", flush=True)

    def do_GET(self) -> None:
        if not self._authorized():
            return
        if self.path == "/v1/status":
            self._json(200, self.server.manager.status())
            return
        self._json(404, {"detail": "Not found"})

    def do_POST(self) -> None:
        if not self._authorized():
            return
        try:
            if self.path == "/v1/check":
                self._json(202, self.server.manager.check())
                return
            if self.path == "/v1/update":
                payload = self._read_json()
                target_commit = payload.get("target_commit")
                if not isinstance(target_commit, str):
                    raise UpdateAgentError("缺少目标 Commit。")
                self._json(202, self.server.manager.start_update(target_commit))
                return
            self._json(404, {"detail": "Not found"})
        except UpdateConflictError as error:
            self._json(409, {"detail": str(error)})
        except UpdateAgentError as error:
            self._json(422, {"detail": str(error)})

    def _read_json(self) -> dict[str, object]:
        content_length = self.headers.get("Content-Length", "0")
        try:
            size = int(content_length)
        except ValueError as error:
            raise UpdateAgentError("请求体长度无效。") from error
        if size < 0 or size > 4096:
            raise UpdateAgentError("请求体过大。")
        try:
            value = json.loads(self.rfile.read(size) or b"{}")
        except json.JSONDecodeError as error:
            raise UpdateAgentError("请求体不是有效 JSON。") from error
        if not isinstance(value, dict):
            raise UpdateAgentError("请求体必须是 JSON 对象。")
        return value


    def _authorized(self) -> bool:
        expected = f"Bearer {self.server.secret}"
        actual = self.headers.get("Authorization", "")
        if not hmac.compare_digest(actual, expected):
            self._json(401, {"detail": "Unauthorized"})
            return False
        return True

    def _json(self, status_code: int, value: dict[str, object]) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def config_from_environment() -> AgentConfig:
    repository = Path(os.environ.get("SAGE_UPDATE_REPOSITORY", "")).resolve()
    socket_path = Path(os.environ.get("SAGE_UPDATE_AGENT_SOCKET", "/run/sage-updater/updater.sock"))
    state_directory = Path(os.environ.get("SAGE_UPDATE_STATE_DIRECTORY", "/var/lib/sage-updater"))
    secret = os.environ.get("SAGE_UPDATE_AGENT_SECRET", "")
    if not str(repository) or str(repository) == "/" or not repository.is_dir():
        raise UpdateAgentError("SAGE_UPDATE_REPOSITORY 必须指向 SageDataManager 仓库。")
    if len(secret) < 32:
        raise UpdateAgentError("SAGE_UPDATE_AGENT_SECRET 至少需要 32 个字符。")
    docker_command, compose_command = resolve_container_commands()
    return AgentConfig(
        repository=repository,
        socket_path=socket_path,
        state_directory=state_directory,
        secret=secret,
        expected_remote=os.environ.get("SAGE_UPDATE_REMOTE_URL", EXPECTED_REMOTE),
        backend_health_url=os.environ.get(
            "SAGE_UPDATE_BACKEND_HEALTH_URL",
            "http://127.0.0.1:8000/api/ready",
        ),
        frontend_health_url=os.environ.get(
            "SAGE_UPDATE_FRONTEND_HEALTH_URL",
            "http://127.0.0.1:8080/",
        ),
        backup_retention_count=int(os.environ.get("SAGE_UPDATE_BACKUP_RETENTION", "10")),
        docker_command=docker_command,
        compose_command=compose_command,
    )


def serve(config: AgentConfig) -> NoReturn:
    config.socket_path.parent.mkdir(parents=True, exist_ok=True)
    if config.socket_path.exists():
        if not config.socket_path.is_socket():
            raise UpdateAgentError(f"Socket 路径已被普通文件占用：{config.socket_path}")
        config.socket_path.unlink()
    manager = UpdateManager(config)
    server = AgentHTTPServer(config.socket_path, manager, config.secret)
    config.socket_path.chmod(0o660)

    def stop_server(signum: int, frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        config.socket_path.unlink(missing_ok=True)
    raise SystemExit(0)


def main() -> NoReturn:
    parser = argparse.ArgumentParser(description="Restricted SageDataManager host update agent")
    parser.parse_args()
    try:
        serve(config_from_environment())
    except UpdateAgentError as error:
        parser.error(str(error))
    raise SystemExit(2)


if __name__ == "__main__":
    main()
