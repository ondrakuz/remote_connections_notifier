#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="connot.service"
USER_SERVICE_DIR="${HOME}/.config/systemd/user"

echo "=== Outer Connection Notificator — Installer ==="
echo "Repo directory: ${SCRIPT_DIR}"

# Make scripts executable
chmod +x "${SCRIPT_DIR}/connot.sh" 2>/dev/null && echo "✔ connot.sh marked executable" || echo "⚠ connot.sh not found, skipping"
chmod +x "${SCRIPT_DIR}/connot_daemon.py" 2>/dev/null && echo "✔ connot_daemon.py marked executable" || echo "⚠ connot_daemon.py not found, skipping"

# Create user systemd directory if needed
mkdir -p "${USER_SERVICE_DIR}"

# Copy service file and update ExecStart to use the absolute path
cp "${SCRIPT_DIR}/${SERVICE_NAME}" "${USER_SERVICE_DIR}/${SERVICE_NAME}"
sed -i "s|ExecStart=.*|ExecStart=/usr/bin/env python3 ${SCRIPT_DIR}/connot_daemon.py|" "${USER_SERVICE_DIR}/${SERVICE_NAME}"
echo "✔ Service file installed to ${USER_SERVICE_DIR}/${SERVICE_NAME}"

# Reload systemd
systemctl --user daemon-reload
echo "✔ systemd user daemon reloaded"

# Enable and start
ENABLE=false
if [[ "${1:-}" == "--enable" ]]; then
    ENABLE=true
else
    read -rp "Enable and start the service now? [y/N] " answer
    [[ "${answer}" =~ ^[Yy]$ ]] && ENABLE=true
fi

if [[ "${ENABLE}" == true ]]; then
    systemctl --user enable "${SERVICE_NAME}"
    systemctl --user start "${SERVICE_NAME}"
    echo "✔ Service enabled and started"
else
    echo "⏭ Skipped enabling the service"
fi

echo ""
echo "Useful commands:"
echo "  systemctl --user status  ${SERVICE_NAME}"
echo "  systemctl --user start   ${SERVICE_NAME}"
echo "  systemctl --user stop    ${SERVICE_NAME}"
echo "  systemctl --user enable  ${SERVICE_NAME}"
echo "  systemctl --user disable ${SERVICE_NAME}"
echo "  journalctl --user -u ${SERVICE_NAME} -f"
