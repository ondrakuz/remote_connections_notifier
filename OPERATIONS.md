# ConnNotify Operations

## Purpose

This document is for operating, verifying, and troubleshooting ConnNotify in day-to-day use.

ConnNotify runs as two services:

- `connot-collector.service` as a system service running as root
- `connot.service` as a user service running in the KDE session

## Deployment Checklist

1. Install the services:

```bash
./install.sh --enable
```

2. Verify the collector is running:

```bash
sudo systemctl status connot-collector.service
```

3. Verify the notifier is running:

```bash
systemctl --user status connot.service
```

4. Verify the event queue exists:

```bash
ls -l /run/connot/events
```

5. Verify the user session bus exists:

```bash
echo "$DBUS_SESSION_BUS_ADDRESS"
ls -l /run/user/$(id -u)/bus
```

## Routine Commands

Restart both services after code or configuration changes:

```bash
sudo systemctl restart connot-collector.service
systemctl --user restart connot.service
```

Tail logs:

```bash
journalctl -u connot-collector.service -f
journalctl --user -u connot.service -f
```

Use the helper script for foreground notifier testing:

```bash
./connot.sh fg
```

## Expected Runtime Artifacts

Installed files:

- `/opt/connot/connot_daemon.py`
- `/opt/connot/connot_notifier.py`
- `/etc/systemd/system/connot-collector.service`
- `~/.config/systemd/user/connot.service`

Runtime files:

- `/run/connot/events/*.json` for queued events
- `/run/user/<uid>/connnotify.log` for `connot.sh` notifier runs
- `/run/user/<uid>/connot_notifier.last` for notifier queue position

## Notification Behavior

Persistent events are intended to remain in KDE history:

- socket burst summaries
- Bluetooth connect/disconnect
- Bluetooth device add/remove
- rfkill block/unblock
- RFCOMM add/remove
- USB NIC add/remove

Transient events are intended to appear as popups without cluttering KDE history:

- individual inbound socket events
- Bluetooth RSSI proximity updates
- NetworkManager state/property churn
- wpa_supplicant state churn
- NFC property chatter

All events are still logged by the collector and notifier.

## Example Socket Notification

Typical popup body:

```text
Visible peer: 203.0.113.20:53214
Peer type: public IPv4
Local endpoint: 192.168.1.10:22 (ssh)
Target process: sshd (pid 1234)
Origin note: last visible hop only; NAT, VPN, proxy, relay, or tunnel may hide the original device.
```

This describes the last visible network peer and the local process that accepted the connection when available.

## Example Log Lines

Collector:

```text
[2026-03-30 18:21:14] [INFO] CONNOT: Socket details: Visible peer: 203.0.113.20:53214 | Peer type: public IPv4 | Local endpoint: 192.168.1.10:22 (ssh) | Target process: sshd (pid 1234) | Origin note: last visible hop only; NAT, VPN, proxy, relay, or tunnel may hide the original device.
```

Notifier:

```text
[2026-03-30 18:21:14] [INFO] CONNOT-NOTIFIER: Delivered [warning] [transient] Inbound TCP connection: Visible peer: 203.0.113.20:53214
```

Burst summary:

```text
[2026-03-30 18:21:18] [INFO] CONNOT-NOTIFIER: Delivered [info] [persistent] Multiple inbound connections: 4 new inbound socket event(s) aggregated.
```

## Known Limits

- The visible remote peer may be only the last hop, not the true original device.
- `ss -p` does not always expose process metadata for every socket.
- Root improves visibility, but cannot generically recover upstream client identity hidden by NAT, proxies, relays, or tunnels.
- The notifier intentionally baselines pre-existing queued events at startup, so it does not replay old events after restarts.

## Troubleshooting

If the collector is running but KDE shows nothing:

1. Check the user notifier:

```bash
systemctl --user status connot.service
journalctl --user -u connot.service -n 100 --no-pager
```

2. Check whether queue files are being created:

```bash
ls -ltr /run/connot/events | tail
```

3. Check whether the notifier is advancing its state file:

```bash
cat /run/user/$(id -u)/connot_notifier.last
```

If process names are missing:

1. Confirm the collector is running as root.
2. Inspect live `ss -p` output manually:

```bash
sudo ss -Htnup
sudo ss -Hltnup
```

3. If `ss` itself does not show the process, ConnNotify cannot invent it.

If KDE history is too noisy:

1. Confirm whether the noisy events are marked `persistent` or `transient` in notifier logs.
2. Tighten the delivery policy in `EventPolicy` inside `connot_daemon.py`.
3. Restart both services after the change.

If `/run/connot/events` grows too much:

1. Check collector logs for pruning failures.
2. Confirm `connot-collector.service` is still running.
3. Confirm the notifier is consuming new files.

## Upgrade Procedure

After pulling new code:

```bash
./install.sh
sudo systemctl restart connot-collector.service
systemctl --user restart connot.service
```

If the service definitions changed:

```bash
sudo systemctl daemon-reload
systemctl --user daemon-reload
sudo systemctl restart connot-collector.service
systemctl --user restart connot.service
```
