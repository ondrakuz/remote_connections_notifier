#!/usr/bin/env bash
set -euo pipefail

# ConnNotify — launcher script for the connot_daemon.py daemon.
# Ensures single-instance execution, dependency checks, and manages
# the daemon lifecycle (start/stop/restart/status/foreground).

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

# Directory where this script lives (used to find connot_daemon.py)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_SCRIPT="$SCRIPT_DIR/connot_daemon.py"

# ---------------------------------------------------------------------------
# Environment defaults
# ---------------------------------------------------------------------------

# D-Bus session address — needed for notify-send to reach the desktop
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"
fi

# XDG runtime directory — used for lock/pid/log files
if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi

LOCK_FILE="$XDG_RUNTIME_DIR/connnotify.lock"
PID_FILE="$XDG_RUNTIME_DIR/connnotify.pid"
LOG_FILE="$XDG_RUNTIME_DIR/connnotify.log"

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------

missing=()
for cmd in python3 notify-send ss; do
    if ! command -v "$cmd" &>/dev/null; then
        missing+=("$cmd")
    fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
    echo "ERROR: Missing required commands: ${missing[*]}" >&2
    exit 1
fi

if [[ ! -f "$DAEMON_SCRIPT" ]]; then
    echo "ERROR: Daemon script not found: $DAEMON_SCRIPT" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

# Return the PID of a running daemon, or empty string if not running.
get_running_pid() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(<"$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return
        fi
    fi
    echo ""
}

do_start() {
    # Use flock to guarantee single instance
    (
        flock -n 9 || { echo "ERROR: Another instance is already running." >&2; exit 1; }

        local pid
        pid=$(get_running_pid)
        if [[ -n "$pid" ]]; then
            echo "Daemon is already running (PID $pid)."
            exit 0
        fi

        echo "Starting ConnNotify daemon..."
        nohup python3 "$DAEMON_SCRIPT" >>"$LOG_FILE" 2>&1 &
        local new_pid=$!
        echo "$new_pid" > "$PID_FILE"
        echo "Daemon started (PID $new_pid). Log: $LOG_FILE"
    ) 9>"$LOCK_FILE"
}

do_stop() {
    local pid
    pid=$(get_running_pid)
    if [[ -z "$pid" ]]; then
        echo "Daemon is not running."
        return 0
    fi

    echo "Stopping ConnNotify daemon (PID $pid)..."
    kill "$pid" 2>/dev/null || true
    # Wait briefly for graceful shutdown
    for _ in $(seq 1 10); do
        if ! kill -0 "$pid" 2>/dev/null; then
            break
        fi
        sleep 0.2
    done

    # Force-kill if still alive
    if kill -0 "$pid" 2>/dev/null; then
        echo "Daemon did not exit gracefully, sending SIGKILL..."
        kill -9 "$pid" 2>/dev/null || true
    fi

    rm -f "$PID_FILE"
    echo "Daemon stopped."
}

do_status() {
    local pid
    pid=$(get_running_pid)
    if [[ -n "$pid" ]]; then
        echo "ConnNotify daemon is running (PID $pid)."
    else
        echo "ConnNotify daemon is not running."
        # Clean up stale PID file
        rm -f "$PID_FILE"
    fi
}

do_foreground() {
    (
        flock -n 9 || { echo "ERROR: Another instance is already running." >&2; exit 1; }

        local pid
        pid=$(get_running_pid)
        if [[ -n "$pid" ]]; then
            echo "Daemon is already running (PID $pid). Stop it first." >&2
            exit 1
        fi

        echo "Running ConnNotify daemon in foreground (Ctrl+C to stop)..."
        python3 "$DAEMON_SCRIPT"
    ) 9>"$LOCK_FILE"
}

# ---------------------------------------------------------------------------
# Subcommand dispatch
# ---------------------------------------------------------------------------

cmd="${1:-start}"

case "$cmd" in
    start)
        do_start
        ;;
    stop)
        do_stop
        ;;
    restart)
        do_stop
        do_start
        ;;
    status)
        do_status
        ;;
    foreground|fg)
        do_foreground
        ;;
    *)
        echo "Usage: $(basename "$0") {start|stop|restart|status|foreground|fg}" >&2
        exit 1
        ;;
esac
