#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer
from typing import BinaryIO, NoReturn

BUSY_STATES = {"checking", "backing_up", "pulling", "building", "restarting", "verifying"}
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
    backend_health_url: str = "http://127.0.0.1:8000/api/health"
    frontend_health_url: str = "http://127.0.0.1:8080/api/health"
    docker_command: tuple[str, ...] = ("docker",)
    compose_command: tuple[str, ...] = ("docker", "compose")


@dataclass(frozen=True)
class CommandResult:
    stdout: str


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
            "logs": [],
        }
        try:
            self._replace_state(self._inspect_repository(fetch=False))
        except UpdateAgentError as error:
            self._set_failure(str(error))

    def status(self) -> dict[str, object]:
        with self._lock:
            return json.loads(json.dumps(self._state))

    def check(self) -> dict[str, object]:
        if not self._operation_lock.acquire(blocking=False):
            raise UpdateConflictError("已有系统更新操作正在运行。")
        try:
            self._set_progress("checking", "fetch", "正在读取 origin/main…", reset=True)
            inspected = self._inspect_repository(fetch=True)
            self._replace_state(inspected)
            return self.status()
        except UpdateAgentError as error:
            self._set_failure(str(error))
            raise
        finally:
            self._operation_lock.release()

    def start_update(self) -> dict[str, object]:
        if not self._operation_lock.acquire(blocking=False):
            raise UpdateConflictError("已有系统更新操作正在运行。")
        try:
            self._set_progress("checking", "preflight", "正在校验已检查的 Commit…", reset=True)
            # The explicit check action already fetched origin/main. Reusing that
            # remote-tracking ref avoids a second, potentially slow GitHub request.
            inspected = self._inspect_repository(fetch=False)
            if not inspected["worktree_clean"]:
                detail = "Git 工作区存在未提交内容，拒绝自动更新。"
                self._set_failure(detail)
                raise UpdateAgentError(detail)
            if not inspected["update_available"]:
                self._replace_state(inspected)
                raise UpdateConflictError("当前已经是 origin/main 的最新版本。")
            old_commit = str(inspected["current_commit"])
            target_commit = str(inspected["latest_commit"])
            self._replace_state(
                {
                    **inspected,
                    "state": "backing_up",
                    "phase": "database_backup",
                    "message": "更新任务已接受，正在备份 PostgreSQL。",
                    "started_at": utc_now(),
                    "completed_at": None,
                    "error": None,
                    "backup_path": None,
                }
            )
            thread = threading.Thread(
                target=self._perform_update,
                args=(old_commit, target_commit),
                name="sage-system-update",
                daemon=True,
            )
            thread.start()
            return self.status()
        except Exception:
            self._operation_lock.release()
            raise

    def _perform_update(self, old_commit: str, target_commit: str) -> None:
        backup_path: Path | None = None
        old_images: dict[str, tuple[str, str]] = {}
        try:
            old_images = self._capture_running_images()
            backup_path = self._backup_database(old_commit)
            self._set_value("backup_path", backup_path.name)

            self._set_progress("pulling", "git_merge", "正在 fast-forward 到已检查的 Commit…")
            self._run(
                ["git", "merge", "--ff-only", target_commit],
                timeout=120,
            )
            actual_commit = self._git(["rev-parse", "HEAD"])
            if actual_commit != target_commit:
                raise UpdateAgentError("拉取完成后的 Commit 与检查结果不一致，已停止更新。")

            self._set_progress("building", "docker_build", "正在构建后端与前端镜像…")
            self._run(
                [*self.config.compose_command, "build", "backend", "frontend"],
                timeout=2400,
            )

            self._set_progress("restarting", "compose_up", "正在重建应用容器并运行迁移…")
            self._run(
                [*self.config.compose_command, "up", "-d", "backend", "frontend"],
                timeout=600,
            )

            self._set_progress("verifying", "health_check", "正在等待新版本通过健康检查…")
            self._wait_for_health()
            inspected = self._inspect_repository(fetch=False)
            self._replace_state(
                {
                    **inspected,
                    "state": "succeeded",
                    "phase": "complete",
                    "message": "系统已更新并通过健康检查。",
                    "started_at": self.status().get("started_at"),
                    "completed_at": utc_now(),
                    "error": None,
                    "backup_path": backup_path.name,
                    "logs": self.status().get("logs", []),
                }
            )
        except Exception as error:
            original_error = str(error)
            rollback_error: str | None = None
            try:
                self._append_log("更新失败，正在恢复旧 Commit 和旧应用镜像。")
                self._rollback_application(old_commit, old_images)
            except Exception as restore_error:
                rollback_error = str(restore_error)
            message = "更新失败，旧版本已恢复。"
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
            "logs": self.status().get("logs", []),
        }

    def _backup_database(self, commit: str) -> Path:
        self.config.state_directory.mkdir(parents=True, exist_ok=True)
        backup_directory = self.config.state_directory / "backups"
        backup_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        timestamp = datetime.now(UTC_TIMEZONE).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_directory / f"sage-{timestamp}-{commit[:12]}.dump"
        env_values = read_dotenv(self.config.repository / ".env")
        database_user = env_values.get("POSTGRES_USER", "sage")
        database_name = env_values.get("POSTGRES_DB", "sage")
        self._append_log(f"创建数据库备份 {backup_path.name}")
        with backup_path.open("wb") as output:
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
        backup_path.chmod(0o600)
        if backup_path.stat().st_size == 0:
            raise UpdateAgentError("PostgreSQL 备份为空，拒绝继续更新。")
        return backup_path

    def _capture_running_images(self) -> dict[str, tuple[str, str]]:
        images: dict[str, tuple[str, str]] = {}
        for service in ("backend", "frontend"):
            container_id = self._compose(["ps", "-q", service], allow_empty=True)
            if not container_id:
                continue
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
            if image_id and image_name:
                images[service] = (image_id, image_name)
        return images

    def _rollback_application(
        self,
        old_commit: str,
        old_images: dict[str, tuple[str, str]],
    ) -> None:
        current_commit = self._git(["rev-parse", "HEAD"])
        if current_commit != old_commit:
            self._run(["git", "reset", "--hard", old_commit], timeout=120)
        for image_id, image_name in old_images.values():
            self._run(
                [*self.config.docker_command, "image", "tag", image_id, image_name],
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
            )
            self._wait_for_health()

    def _wait_for_health(self) -> None:
        deadline = time.monotonic() + 240
        last_error = "服务尚未响应"
        while time.monotonic() < deadline:
            healthy = True
            for url in (self.config.backend_health_url, self.config.frontend_health_url):
                try:
                    with urllib.request.urlopen(url, timeout=5) as response:
                        if response.status != 200:
                            healthy = False
                            last_error = f"{url} 返回 HTTP {response.status}"
                except (OSError, urllib.error.URLError) as error:
                    healthy = False
                    last_error = f"{url}：{error}"
            if healthy:
                return
            time.sleep(3)
        raise UpdateAgentError(f"新版本健康检查超时：{last_error}")

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
    ) -> CommandResult:
        try:
            completed = subprocess.run(
                command,
                cwd=self.config.repository,
                check=False,
                stdout=stdout or subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=stdout is None,
                timeout=timeout,
                env=os.environ.copy(),
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
                self._json(200, self.server.manager.check())
                return
            if self.path == "/v1/update":
                self._json(202, self.server.manager.start_update())
                return
            self._json(404, {"detail": "Not found"})
        except UpdateConflictError as error:
            self._json(409, {"detail": str(error)})
        except UpdateAgentError as error:
            self._json(422, {"detail": str(error)})

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
            "http://127.0.0.1:8000/api/health",
        ),
        frontend_health_url=os.environ.get(
            "SAGE_UPDATE_FRONTEND_HEALTH_URL",
            "http://127.0.0.1:8080/api/health",
        ),
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
