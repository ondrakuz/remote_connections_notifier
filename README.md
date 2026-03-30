# Remote Connection Notificator

Monitors **incoming remote connections** on Linux and sends KDE Plasma desktop notifications.

## Architecture

ConnNotify now runs as two components:

- **Collector** (`connot_daemon.py`) — system service running as root, gathers events and writes normalized JSON to `/run/connot/events`
- **Notifier** (`connot_notifier.py`) — user service running in the desktop session, reads queued events and sends KDE notifications

This split allows the collector to see target processes more reliably via `ss -p` while keeping notifications in the correct user session.

### Runtime flow

1. The root collector monitors sockets, Bluetooth, NetworkManager, rfkill, RFCOMM, NFC, and USB NIC changes.
2. Each event is normalized, deduplicated, rate-limited, and annotated with notification delivery metadata.
3. The collector writes one JSON file per event into `/run/connot/events`.
4. The user notifier polls that directory and forwards events to KDE via `notify-send`.

### Why two services

- `ss -p` and process visibility are more reliable as root
- KDE notifications belong in the user session, not in a root-owned service
- separating collection from presentation avoids D-Bus/session issues in system services

## KDE notification policy

- **Persistent in KDE history**: socket burst summaries, Bluetooth connect/disconnect and device add/remove, rfkill changes, RFCOMM add/remove, USB NIC add/remove
- **Transient only**: ordinary per-connection socket popups, Bluetooth RSSI proximity updates, NetworkManager and wpa_supplicant state churn, NFC property chatter

All events are still logged; only the KDE history policy differs.

## Event details

Socket notifications and logs can include:

- visible remote peer address and port
- peer type such as `public IPv4`, `private IPv4`, or `link-local IPv6`
- local endpoint address and service name when resolvable
- target process name and PID when `ss -p` exposes it
- an origin note explaining that the visible peer may only be the last hop because of NAT, VPN, proxy, relay, or tunnel layers

This means the script can often identify the local target service or process, but it still cannot generically reconstruct the true original remote device behind intermediaries.

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
- `sudo` for installing the system collector service

## Install layout

The installer deploys files to:

- `/opt/connot/connot_daemon.py`
- `/opt/connot/connot_notifier.py`
- `/etc/systemd/system/connot-collector.service`
- `~/.config/systemd/user/connot.service`

Runtime state is stored in:

- `/run/connot/events` for collector-to-notifier event files
- `/run/user/<uid>/connnotify.log` for `connot.sh` foreground/background notifier logs
- `/run/user/<uid>/connot_notifier.last` for the last delivered queue entry

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

### Manual service control

```bash
sudo systemctl restart connot-collector.service
systemctl --user restart connot.service
```

Restart the notifier after KDE session changes. Restart the collector after code or service-level monitoring changes.

## Troubleshooting

- If KDE popups do not appear, check `systemctl --user status connot.service` and verify `DBUS_SESSION_BUS_ADDRESS` points at `/run/user/<uid>/bus`.
- If target process names are missing, that usually means `ss -p` did not expose process metadata for that socket, not necessarily that detection is broken.
- If the notifier seems silent after downtime, remember it baselines existing queue entries on startup to avoid replay storms.
- If `/run/connot/events` grows unexpectedly, inspect the collector journal first; the collector prunes old queue files periodically.

## Quick Diagnosis

- `No KDE popup appears`
  Check `systemctl --user status connot.service` and the user journal first.
- `Queue files appear but no notifications are delivered`
  Check `/run/user/<uid>/connot_notifier.last` and the notifier journal.
- `Target process is missing`
  Check whether `sudo ss -Htnup` shows process metadata for that socket.
- `Too many items in KDE history`
  Adjust `EventPolicy` in [connot_daemon.py](/home/henryok/Projekty/Scripts/Bash/connot/connot_daemon.py).
- `Too many queue files`
  Check whether the notifier is running and whether collector pruning still executes.

## Files

| File | Description |
|---|---|
| `connot_daemon.py` | Python 3 root collector — monitors and queues normalized events |
| `connot_notifier.py` | Python 3 user notifier — reads queue and sends KDE notifications |
| `connot.sh` | Bash launcher for the user notifier |
| `connot-collector.service` | systemd system unit for the collector |
| `connot.service` | systemd user unit for the notifier |
| `install.sh` | Installs both services and shared scripts |
| `OPERATIONS.md` | Deployment, verification, troubleshooting, and upgrade playbook |
| `CHANGELOG.md` | High-level summary of notable project changes |
