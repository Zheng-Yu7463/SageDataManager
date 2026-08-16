#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "请使用 sudo 运行：sudo bash deploy/install-updater.sh" >&2
  exit 1
fi

repository="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
environment_file="${repository}/.env"
service_template="${repository}/deploy/sage-updater.service"
service_file="/etc/systemd/system/sage-updater.service"

if [[ ! -d "${repository}/.git" ]]; then
  echo "未找到 Git 仓库：${repository}" >&2
  exit 1
fi
if [[ ! -f "${environment_file}" ]]; then
  echo "未找到 ${environment_file}，请先从 .env.example 创建并配置 .env。" >&2
  exit 1
fi
if [[ "${repository}" == *$'\n'* || "${repository}" == *'&'* || "${repository}" == *'|'* ]]; then
  echo "仓库路径包含 systemd 模板不支持的字符。" >&2
  exit 1
fi

compose_configuration="$(cd "${repository}" && docker compose config --format json)"
current_secret="$(
  python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["backend"]["environment"].get("SAGE_UPDATE_AGENT_SECRET", ""))' \
    <<<"${compose_configuration}"
)"
unset compose_configuration
if [[ -z "${current_secret}" ]]; then
  if command -v openssl >/dev/null 2>&1; then
    generated_secret="$(openssl rand -hex 32)"
  else
    generated_secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  fi
  printf '\nSAGE_UPDATE_AGENT_SECRET=%s\n' "${generated_secret}" >>"${environment_file}"
  chmod 600 "${environment_file}"
  echo "已在 .env 中生成更新代理密钥。"
fi

sed "s|@@REPOSITORY@@|${repository}|g" "${service_template}" >"${service_file}"
chmod 644 "${service_file}"
systemctl daemon-reload
systemctl enable sage-updater.service
systemctl restart sage-updater.service

for attempt in {1..20}; do
  [[ -S /run/sage-updater/updater.sock ]] && break
  sleep 0.25
done
if [[ ! -S /run/sage-updater/updater.sock ]]; then
  systemctl status sage-updater.service --no-pager >&2 || true
  echo "更新代理未能创建 Unix Socket。" >&2
  exit 1
fi

cd "${repository}"
release_commit="$(git rev-parse HEAD)"
SAGE_RELEASE_COMMIT="${release_commit}" docker compose up --build -d --force-recreate --no-deps backend
SAGE_RELEASE_COMMIT="${release_commit}" docker compose up --build -d frontend

echo
echo "SageDataManager 更新代理已启用。"
echo "服务状态：systemctl status sage-updater --no-pager"
echo "服务日志：journalctl -u sage-updater -f"
