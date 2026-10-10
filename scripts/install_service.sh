#!/usr/bin/env bash
# Install LedgerMind API + scheduler systemd units (system scope). Run with sudo.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_TEMPLATE="${ROOT}/deploy/systemd/ledgermind-api.service"
SCHED_TEMPLATE="${ROOT}/deploy/systemd/ledgermind-scheduler.service"
API_TARGET="/etc/systemd/system/ledgermind-api.service"
SCHED_TARGET="/etc/systemd/system/ledgermind-scheduler.service"
RUN_USER="${SUDO_USER:-${USER:-root}}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/install_service.sh" >&2
  exit 1
fi

for f in "$API_TEMPLATE" "$SCHED_TEMPLATE"; do
  if [[ ! -f "$f" ]]; then
    echo "Missing template: $f" >&2
    exit 1
  fi
done

if [[ ! -x "${ROOT}/.venv/bin/python" ]]; then
  echo "Create venv first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [[ ! -f "${ROOT}/.env" ]]; then
  echo "Warning: ${ROOT}/.env not found — copy .env.example and configure before start." >&2
fi

echo "LedgerMind systemd install"
echo "  Repo root:     ${ROOT}"
echo "  Run as user:   ${RUN_USER}"
echo "  API unit  →    ${API_TARGET}"
echo "  Scheduler →    ${SCHED_TARGET}"
echo ""
read -r -p "Install these units? [y/N] " confirm
if [[ "${confirm,,}" != "y" ]]; then
  echo "Aborted."
  exit 0
fi

render_unit() {
  local template="$1" dest="$2"
  sed -e "s|REPLACE_ROOT|${ROOT}|g" -e "s|REPLACE_USER|${RUN_USER}|g" "$template" >"$dest"
  chmod 644 "$dest"
}

render_unit "$API_TEMPLATE" "$API_TARGET"
render_unit "$SCHED_TEMPLATE" "$SCHED_TARGET"

systemctl daemon-reload
systemctl enable ledgermind-api.service ledgermind-scheduler.service
systemctl restart ledgermind-api.service
systemctl restart ledgermind-scheduler.service

echo ""
echo "Installed and started."
echo "  systemctl status ledgermind-api"
echo "  systemctl status ledgermind-scheduler"
echo "  journalctl -u ledgermind-scheduler -f"
