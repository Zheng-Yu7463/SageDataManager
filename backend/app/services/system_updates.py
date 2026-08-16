from __future__ import annotations

import http.client
import json
import re
import socket
from pathlib import Path
from typing import Any

from app.core.config import settings

MAX_UPDATE_AGENT_RESPONSE_BYTES = 1_000_000
UPDATE_AGENT_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class UpdateAgentUnavailableError(Exception):
    pass


class UpdateAgentRequestError(Exception):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class UnixSocketHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: Path, timeout: int) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        connection.connect(str(self.socket_path))
        self.sock = connection


def update_agent_configured() -> bool:
    return bool(settings.update_agent_secret and settings.update_agent_socket)


def disabled_update_status(message: str | None = None) -> dict[str, Any]:
    return {
        "enabled": False,
        "state": "unavailable",
        "phase": None,
        "message": message or "宿主机更新服务尚未配置。",
        "update_available": False,
        "behind_count": 0,
        "ahead_count": 0,
        "commits": [],
        "logs": [],
    }


def request_update_agent(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not update_agent_configured():
        raise UpdateAgentUnavailableError("宿主机更新服务尚未配置。")

    connection = UnixSocketHTTPConnection(
        settings.update_agent_socket,
        settings.update_agent_timeout_seconds,
    )
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {settings.update_agent_secret}",
    }
    request_body = None
    if payload is not None:
        request_body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        connection.request(
            method,
            path,
            body=request_body if request_body is not None else (b"" if method == "POST" else None),
            headers=headers,
        )
        response = connection.getresponse()
        raw_body = response.read(MAX_UPDATE_AGENT_RESPONSE_BYTES + 1)
    except (OSError, TimeoutError, http.client.HTTPException) as error:
        raise UpdateAgentUnavailableError("无法连接宿主机更新服务。") from error
    finally:
        connection.close()

    if len(raw_body) > MAX_UPDATE_AGENT_RESPONSE_BYTES:
        raise UpdateAgentRequestError("宿主机更新服务返回的响应过大。")

    try:
        body = json.loads(raw_body or b"{}")
    except json.JSONDecodeError as error:
        raise UpdateAgentRequestError("宿主机更新服务返回了无效响应。") from error
    if not isinstance(body, dict):
        raise UpdateAgentRequestError("宿主机更新服务返回了无效响应。")
    if response.status >= 400:
        detail = body.get("detail")
        code = body.get("code")
        raise UpdateAgentRequestError(
            detail if isinstance(detail, str) else "宿主机更新服务拒绝了请求。",
            code=code
            if isinstance(code, str) and UPDATE_AGENT_ERROR_CODE_PATTERN.fullmatch(code)
            else None,
        )
    return body
