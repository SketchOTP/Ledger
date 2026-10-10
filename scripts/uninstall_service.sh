#!/usr/bin/env bash
# Stop/disable LedgerMind systemd units and remove unit files (keeps household data).
set -euo pipefail

API_UNIT="ledgermind-api.service"
SCHED_UNIT="ledgermind-scheduler.service"
API_TARGET="/etc/systemd/system/${API_UNIT}"
SCHED_TARGET="/etc/systemd/system/${SCHED_UNIT}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/uninstall_service.sh" >&2
  exit 1
fi

echo "LedgerMind systemd uninstall"
echo "  Will stop/disable: ${API_UNIT}, ${SCHED_UNIT}"
echo "  Will remove:       ${API_TARGET}"
echo "                     ${SCHED_TARGET}"
echo "  Data in repo data/ is NOT deleted."
echo ""
read -r -p "Proceed? [y/N] " confirm
if [[ "${confirm,,}" != "y" ]]; then
  echo "Aborted."
  exit 0
fi

systemctl stop "${SCHED_UNIT}" 2>/dev/null || true
systemctl stop "${API_UNIT}" 2>/dev/null || true
systemctl disable "${SCHED_UNIT}" 2>/dev/null || true
systemctl disable "${API_UNIT}" 2>/dev/null || true

rm -f "$API_TARGET" "$SCHED_TARGET"
systemctl daemon-reload

echo "Uninstalled. Household data unchanged."
