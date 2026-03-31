#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/connot"
USER_SERVICE_NAME="connot.service"
SYSTEM_SERVICE_NAME="connot-collector.service"
USER_SERVICE_DIR="${HOME}/.config/systemd/user"
SYSTEM_SERVICE_DIR="/etc/systemd/system"

run_root() {
    if [[ "$(id -u)" -eq 0 ]]; then
        "$@"
    else
        sudo "$@"
    fi
}

echo "=== Connection Notifier — Two-component Installer ==="
echo "Repo directory: ${SCRIPT_DIR}"
echo "Install directory: ${INSTALL_DIR}"

# Make scripts executable
chmod +x "${SCRIPT_DIR}/connot.sh" 2>/dev/null && echo "✔ connot.sh marked executable" || echo "⚠ connot.sh not found, skipping"
chmod +x "${SCRIPT_DIR}/connot_daemon.py" 2>/dev/null && echo "✔ connot_daemon.py marked executable" || echo "⚠ connot_daemon.py not found, skipping"
chmod +x "${SCRIPT_DIR}/connot_notifier.py" 2>/dev/null && echo "✔ connot_notifier.py marked executable" || echo "⚠ connot_notifier.py not found, skipping"

# Install shared scripts for both services
run_root mkdir -p "${INSTALL_DIR}"
run_root install -m 0755 "${SCRIPT_DIR}/connot_daemon.py" "${INSTALL_DIR}/connot_daemon.py"
run_root install -m 0755 "${SCRIPT_DIR}/connot_notifier.py" "${INSTALL_DIR}/connot_notifier.py"
echo "✔ Scripts installed to ${INSTALL_DIR}"

# Create user systemd directory if needed
mkdir -p "${USER_SERVICE_DIR}"

# Install user notifier service
cp "${SCRIPT_DIR}/${USER_SERVICE_NAME}" "${USER_SERVICE_DIR}/${USER_SERVICE_NAME}"
sed -i "s|ExecStart=.*|ExecStart=/usr/bin/env python3 ${INSTALL_DIR}/connot_notifier.py|" "${USER_SERVICE_DIR}/${USER_SERVICE_NAME}"
echo "✔ User service installed to ${USER_SERVICE_DIR}/${USER_SERVICE_NAME}"

# Install system collector service
run_root cp "${SCRIPT_DIR}/${SYSTEM_SERVICE_NAME}" "${SYSTEM_SERVICE_DIR}/${SYSTEM_SERVICE_NAME}"
run_root sed -i "s|ExecStart=.*|ExecStart=/usr/bin/env python3 ${INSTALL_DIR}/connot_daemon.py|" "${SYSTEM_SERVICE_DIR}/${SYSTEM_SERVICE_NAME}"
echo "✔ System service installed to ${SYSTEM_SERVICE_DIR}/${SYSTEM_SERVICE_NAME}"

# Reload systemd
systemctl --user daemon-reload
run_root systemctl daemon-reload
echo "✔ systemd user and system daemons reloaded"

# Enable and start
ENABLE=false
if [[ "${1:-}" == "--enable" ]]; then
    ENABLE=true
else
    read -rp "Enable and start both collector and notifier now? [y/N] " answer
    [[ "${answer}" =~ ^[Yy]$ ]] && ENABLE=true
fi

if [[ "${ENABLE}" == true ]]; then
    run_root systemctl enable "${SYSTEM_SERVICE_NAME}"
    run_root systemctl restart "${SYSTEM_SERVICE_NAME}"
    systemctl --user enable "${USER_SERVICE_NAME}"
    systemctl --user restart "${USER_SERVICE_NAME}"
    echo "✔ Collector and notifier enabled and started"
else
    echo "⏭ Skipped enabling the services"
fi

echo ""
echo "Useful commands:"
echo "  systemctl status ${SYSTEM_SERVICE_NAME}"
echo "  systemctl start ${SYSTEM_SERVICE_NAME}"
echo "  systemctl stop ${SYSTEM_SERVICE_NAME}"
echo "  journalctl -u ${SYSTEM_SERVICE_NAME} -f"
echo "  systemctl --user status ${USER_SERVICE_NAME}"
echo "  systemctl --user start ${USER_SERVICE_NAME}"
echo "  systemctl --user stop ${USER_SERVICE_NAME}"
echo "  journalctl --user -u ${USER_SERVICE_NAME} -f"
