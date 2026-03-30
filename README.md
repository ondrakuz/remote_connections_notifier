# Remote Connection Notificator

Monitors **incoming remote connections** on Linux and sends KDE Plasma desktop notifications.

## Architecture

ConnNotify now runs as two components:

- **Collector** (`connot_daemon.py`) — system service running as root, gathers events and writes normalized JSON to `/run/connot/events`
- **Notifier** (`connot_notifier.py`) — user service running in the desktop session, reads queued events and sends KDE notifications

This split allows the collector to see target processes more reliably via `ss -p` while keeping notifications in the correct user session.

## What it monitors

| Source | Method | Interval |
|---|---|---|
| **Bluetooth** (connect/disconnect/nearby) | BlueZ D-Bus signals | event-driven |
| **NetworkManager** (link up/down, Wi-Fi, Ethernet) | NM D-Bus signals | event-driven |
| **wpa_supplicant** (Wi-Fi state transitions) | D-Bus signals | event-driven |
| **NFC** (tag/device detected) | neard D-Bus signals | event-driven |
| **Inbound TCP/UDP** connections | `ss -p` polling | 2s |
| **rfkill** radio block/unblock | `/sys/class/rfkill` | 5s |
| **RFCOMM** serial-over-Bluetooth | `/dev/rfcomm*` | 5s |
| **USB network adapters** added/removed | `/sys/class/net` | 5s |

## Anti-spam

- **10-second warmup** — existing state is baselined, not notified
- **Per-source cooldowns** (BT: 30s, Ethernet: 5s, Wi-Fi: 15s, sockets: 60s)
- **Burst aggregation** — >3 socket events in 3s are merged into one notification
- **Flap damping** — 3+ toggles in 60s suppressed
- **Noisy traffic filtered** — loopback, link-local, mDNS, LLMNR, SSDP, NBNS, DHCP

## Requirements

- Ubuntu/KDE Plasma (tested on Ubuntu 24.04 + Plasma 5.27)
- Python 3 with `python3-dbus` and `python3-gi`
- `notify-send` (`libnotify-bin`)
- `ss` (`iproute2`)

## Usage

### Quick start (foreground notifier only)

```bash
./connot.sh fg
```

For end-to-end monitoring, run the collector as a system service and the notifier as a user service.

### Notifier mode

```bash
./connot.sh start     # start in background
./connot.sh stop      # stop notifier
./connot.sh restart   # restart
./connot.sh status    # check if running
```

### Install both services

```bash
./install.sh            # interactive
./install.sh --enable   # install + enable + start
```

Then manage with:

```bash
sudo systemctl status connot-collector.service
journalctl -u connot-collector.service -f
systemctl --user status connot.service
journalctl --user -u connot.service -f
```

## Files

| File | Description |
|---|---|
| `connot_daemon.py` | Python 3 root collector — monitors and queues normalized events |
| `connot_notifier.py` | Python 3 user notifier — reads queue and sends KDE notifications |
| `connot.sh` | Bash launcher for the user notifier |
| `connot-collector.service` | systemd system unit for the collector |
| `connot.service` | systemd user unit for the notifier |
| `install.sh` | Installs both services and shared scripts |
