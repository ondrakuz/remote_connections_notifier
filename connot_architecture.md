# ConnNotify Architecture

## Overview

ConnNotify now uses two cooperating processes:

- **Collector** (`connot_daemon.py`) runs as root and uses a **GLib main loop** with event-driven monitors and pollers
- **Notifier** (`connot_notifier.py`) runs in the user session and forwards queued events to KDE

The collector performs **Normalize → Deduplicate → Queue**, and the notifier performs **Read queue → Notify**.

- **Event-driven** (D-Bus signals) — zero-latency, no polling
- **Pollers** — periodic checks at 2–5 second intervals

All events flow through a unified pipeline: **Normalize → Deduplicate → Notify**.

## Diagram

```mermaid
flowchart TB
    subgraph "Event-Driven (D-Bus)"
        BT[Bluetooth\nBlueZ D-Bus]
        NM[NetworkManager\nD-Bus]
        WPA[wpa_supplicant\nD-Bus]
        NFC[NFC / neard\nD-Bus]
    end

    subgraph "Pollers"
        SS[Inbound TCP/UDP\nss every 2s]
        RF[rfkill radios\n/sys every 5s]
        RFC[RFCOMM\n/dev every 5s]
        USB[USB NICs\n/sys every 5s]
    end

    subgraph "Collector Core"
        NORM[EventNormalizer]
        CACHE[StateCache\ncooldown + burst + flap]
        QUEUE[EventQueuePublisher\n/run/connot/events]
    end

    subgraph "User Session"
        NOTIFY[NotificationManager]
        KDE[KDE Plasma\npopup + panel]
    end

    BT --> NORM
    NM --> NORM
    WPA --> NORM
    NFC --> NORM
    SS --> NORM
    RF --> NORM
    RFC --> NORM
    USB --> NORM

    NORM --> CACHE
    CACHE --> QUEUE
    QUEUE --> NOTIFY
    NOTIFY --> KDE
```

## Monitors

| Monitor | Source | Method | Interval | File Reference |
|---|---|---|---|---|
| Bluetooth | BlueZ `org.bluez` | D-Bus signals (`InterfacesAdded`, `PropertiesChanged`) | event-driven | `connot_daemon.py` L258–328 |
| NetworkManager | `org.freedesktop.NetworkManager` | D-Bus signals (`DeviceAdded`, `StateChanged`, `PropertiesChanged`) | event-driven | `connot_daemon.py` L332–410 |
| wpa_supplicant | `fi.w1.wpa_supplicant1` | D-Bus signals (`StateChanged`, `PropertiesChanged`) | event-driven | `connot_daemon.py` L414–453 |
| NFC | neard `org.neard` | D-Bus signals (`InterfacesAdded/Removed`) | event-driven | `connot_daemon.py` L457–505 |
| Inbound TCP/UDP | `ss -Htnup` / `ss -Hltnup` | subprocess polling | 2s | `connot_daemon.py` |
| rfkill radios | `/sys/class/rfkill/` | sysfs polling | 5s | `connot_daemon.py` L597–640 |
| RFCOMM | `/dev/rfcomm*` | glob polling | 5s | `connot_daemon.py` L644–673 |
| USB NICs | `/sys/class/net/*/device` | sysfs polling | 5s | `connot_daemon.py` L677–720 |

## Anti-Spam Pipeline

```
Event → Cooldown check → Flap damping → Burst aggregation → Notification
```

| Mechanism | Description | Config |
|---|---|---|
| **Warmup** | 10s baseline at startup — no notifications for existing state | `WARMUP_SECONDS = 10` |
| **Cooldowns** | Per-source minimum interval between repeated notifications | BT: 30s, Ethernet: 5s, Wi-Fi: 15s, Socket: 60s, rfkill: 10s |
| **Flap damping** | Suppresses after 3+ toggles of the same key within 60s | `FLAP_COUNT = 3`, `FLAP_WINDOW = 60s` |
| **Burst aggregation** | >3 socket events in 3s merged into one summary notification | `BURST_THRESHOLD = 3`, `BURST_WINDOW = 3s` |
| **Noise filter** | Loopback, link-local, mDNS (5353), LLMNR (5355), SSDP (1900), NBNS (137), DHCP (67/68) | `NOISY_UDP_PORTS` |

## Notification Path

1. Collector writes one JSON event per file into `/run/connot/events`
2. User notifier polls that directory
3. Primary desktop notification backend: `notify-send -a "ConnNotify" -i <icon> "<title>" "<body>"`
4. Fallback: `kdialog --passivepopup`

## Files

| File | Role |
|---|---|
| `connot_daemon.py` | Python 3 root collector — monitors, event pipeline, event queue writer |
| `connot_notifier.py` | Python 3 user notifier — queue reader and KDE notification sender |
| `connot.sh` | Bash launcher for the notifier only |
| `connot-collector.service` | systemd system service unit |
| `connot.service` | systemd user service unit |
| `install.sh` | Installer for both services |
