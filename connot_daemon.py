#!/usr/bin/env python3
# chmod +x connot_daemon.py
"""
connot_daemon.py — Connection Notificator Daemon

Monitors incoming remote connections on Linux and sends KDE Plasma
desktop notifications via notify-send.

Monitors:
  - Bluetooth (BlueZ D-Bus)
  - NetworkManager (D-Bus)
  - wpa_supplicant (D-Bus)
  - Inbound TCP/UDP sockets (polling ss)
  - rfkill radio state (polling /sys)
  - RFCOMM serial ports (polling /dev)
  - NFC via neard (D-Bus, if available)
  - USB network adapters (polling /sys)

Dependencies (all pre-installed on Ubuntu 24.04 + Plasma):
  - python3-dbus, python3-gi, notify-send (libnotify-bin)
"""

import glob
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(msg, level="INFO"):
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{ts}] [{level}] CONNOT: {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WARMUP_SECONDS = 10

NOISY_UDP_PORTS = {5353, 5355, 1900, 137, 67, 68}

ICON_MAP = {
    "bluetooth": "bluetooth-active",
    "wifi": "network-wireless",
    "ethernet": "network-wired",
    "usb_nic": "drive-removable-media-usb",
    "socket": "network-server",
    "nfc": "network-wireless",
    "rfkill": "dialog-warning",
    "rfcomm": "bluetooth-active",
    "networkmanager": "network-wired",
    "wpa_supplicant": "network-wireless",
    "warning": "dialog-warning",
}

COOLDOWNS = {
    "bluetooth": 30,
    "ethernet": 5,
    "wifi": 15,
    "socket": 60,
    "rfkill": 10,
    "rfcomm": 30,
    "nfc": 30,
    "usb_nic": 10,
    "networkmanager": 5,
    "wpa_supplicant": 15,
}

BURST_THRESHOLD = 3
BURST_WINDOW = 3.0
FLAP_COUNT = 3
FLAP_WINDOW = 60.0
RUN_DIR = "/run/connot"
QUEUE_DIR = f"{RUN_DIR}/events"
QUEUE_RETENTION_SECONDS = 3600
QUEUE_MAX_FILES = 2048


# ---------------------------------------------------------------------------
# EventQueuePublisher
# ---------------------------------------------------------------------------

class EventQueuePublisher:
    """Persist normalized events for the user-side notifier."""

    @staticmethod
    def ensure_queue_dir():
        try:
            os.makedirs(QUEUE_DIR, mode=0o755, exist_ok=True)
        except OSError as exc:
            log(f"Failed to create queue directory {QUEUE_DIR}: {exc}", "WARN")
            return False
        return True

    @classmethod
    def publish(cls, event):
        if not cls.ensure_queue_dir():
            return

        event["emitted_ts"] = time.time()
        filename = f"{int(event['emitted_ts'] * 1000):013d}_{os.getpid()}_{time.time_ns()}.json"
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                dir=QUEUE_DIR,
                prefix=".tmp_",
                suffix=".json",
                delete=False,
                encoding="utf-8",
            ) as handle:
                json.dump(event, handle, ensure_ascii=True)
                handle.write("\n")
                tmp_path = handle.name
            os.chmod(tmp_path, 0o644)
            os.replace(tmp_path, os.path.join(QUEUE_DIR, filename))
        except OSError as exc:
            log(f"Failed to publish event {event['key']}: {exc}", "WARN")
            try:
                if "tmp_path" in locals():
                    os.unlink(tmp_path)
            except OSError:
                pass

    @classmethod
    def prune(cls):
        if not os.path.isdir(QUEUE_DIR):
            return

        now = time.time()
        try:
            entries = sorted(
                entry for entry in os.listdir(QUEUE_DIR) if entry.endswith(".json")
            )
        except OSError as exc:
            log(f"Failed to inspect queue directory {QUEUE_DIR}: {exc}", "WARN")
            return

        stale_cutoff = now - QUEUE_RETENTION_SECONDS
        for entry in entries:
            path = os.path.join(QUEUE_DIR, entry)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            if stat.st_mtime < stale_cutoff:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        overflow = max(0, len(entries) - QUEUE_MAX_FILES)
        if overflow <= 0:
            return

        for entry in entries[:overflow]:
            path = os.path.join(QUEUE_DIR, entry)
            try:
                os.unlink(path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# EventNormalizer
# ---------------------------------------------------------------------------

class EventNormalizer:
    """Produce a uniform event dict from any monitor."""

    @staticmethod
    def normalize(source, kind, key, title, body, icon=None, severity="info"):
        if icon is None:
            icon = ICON_MAP.get(source, "dialog-information")
        return {
            "source": source,
            "kind": kind,
            "key": key,
            "title": title,
            "body": body,
            "icon": icon,
            "severity": severity,
            "ts": time.time(),
        }


# ---------------------------------------------------------------------------
# EventPolicy
# ---------------------------------------------------------------------------

class EventPolicy:
    """Annotate events with KDE delivery preferences."""

    PERSISTENT_SOURCES = {"rfkill", "usb_nic", "rfcomm"}
    PERSISTENT_SOCKET_KINDS = {"burst"}
    PERSISTENT_BLUETOOTH_KINDS = {
        "connected",
        "disconnected",
        "device_added",
        "device_removed",
    }

    @classmethod
    def annotate(cls, event):
        source = event["source"]
        kind = event["kind"]
        severity = event["severity"]

        persistent = False
        if source in cls.PERSISTENT_SOURCES:
            persistent = True
        elif source == "socket" and kind in cls.PERSISTENT_SOCKET_KINDS:
            persistent = True
        elif source == "bluetooth" and kind in cls.PERSISTENT_BLUETOOTH_KINDS:
            persistent = True
        elif severity == "warning" and source in {"nfc"} and kind in {"added", "removed"}:
            persistent = True

        event["delivery"] = {
            "persistent": persistent,
            "transient": not persistent,
            "urgency": "critical" if severity == "warning" else "normal",
            "expire_ms": 12000 if persistent else 5000,
            "category": f"device.{source}" if source != "socket" else "network.inbound",
        }
        return event


# ---------------------------------------------------------------------------
# StateCache
# ---------------------------------------------------------------------------

class StateCache:
    """Deduplication, cooldowns, burst aggregation, and flap damping."""

    def __init__(self):
        self._last_notified = {}   # key -> timestamp
        self._burst_events = {}    # source -> list of timestamps
        self._flap_history = {}    # key -> list of timestamps
        self._burst_pending = {}   # source -> GLib timeout id
        self._burst_queued = {}    # source -> list of events

    def should_notify(self, event):
        key = event["key"]
        source = event["source"]
        now = event["ts"]

        # --- cooldown ---
        cooldown = COOLDOWNS.get(source, 10)
        last = self._last_notified.get(key, 0)
        if now - last < cooldown:
            return False

        # --- flap damping ---
        flaps = self._flap_history.setdefault(key, [])
        flaps.append(now)
        flaps[:] = [t for t in flaps if now - t <= FLAP_WINDOW]
        if len(flaps) > FLAP_COUNT:
            log(f"Flap damping suppressed: {key}", "DEBUG")
            return False

        # --- burst aggregation (socket source) ---
        if source == "socket":
            bursts = self._burst_events.setdefault(source, [])
            bursts.append(now)
            bursts[:] = [t for t in bursts if now - t <= BURST_WINDOW]
            if len(bursts) > BURST_THRESHOLD:
                self._queue_burst(source, event)
                return False

        self._last_notified[key] = now
        return True

    def _queue_burst(self, source, event):
        queue = self._burst_queued.setdefault(source, [])
        queue.append(event)
        if source not in self._burst_pending:
            self._burst_pending[source] = GLib.timeout_add_seconds(
                int(BURST_WINDOW) + 1, self._flush_burst, source
            )

    def _flush_burst(self, source):
        self._burst_pending.pop(source, None)
        queue = self._burst_queued.pop(source, [])
        if queue:
            n = len(queue)
            agg_event = EventNormalizer.normalize(
                source=source,
                kind="burst",
                key=f"burst:{source}:{int(time.time())}",
                title="Multiple inbound connections",
                body=f"{n} new inbound socket event(s) aggregated.",
                icon=ICON_MAP.get(source, "network-server"),
                severity="info",
            )
            EventPolicy.annotate(agg_event)
            self._last_notified[agg_event["key"]] = agg_event["ts"]
            EventQueuePublisher.publish(agg_event)
            log(f"Burst notification: {n} events for {source}")
        return False  # remove timeout


# ---------------------------------------------------------------------------
# Daemon core
# ---------------------------------------------------------------------------

class ConnNotifyDaemon:
    """Main daemon class — wires up all monitors and runs the GLib loop."""

    def __init__(self):
        DBusGMainLoop(set_as_default=True)
        self.loop = GLib.MainLoop()
        self.system_bus = dbus.SystemBus()
        self.state_cache = StateCache()
        self._warming_up = True
        self._warmup_end = time.time() + WARMUP_SECONDS
        self._monitors = []

    # -- event dispatch -----------------------------------------------------

    def dispatch(self, event):
        if self._warming_up:
            if time.time() < self._warmup_end:
                log(f"Warmup baseline: {event['key']}", "DEBUG")
                return
            self._warming_up = False
            log("Warmup complete — notifications enabled.")

        if not self.state_cache.should_notify(event):
            return

        EventPolicy.annotate(event)
        log(f"NOTIFY [{event['severity']}] {event['title']}: {event['body']}")
        EventQueuePublisher.publish(event)

    # -- setup --------------------------------------------------------------

    def setup(self):
        log("Setting up monitors…")
        EventQueuePublisher.ensure_queue_dir()
        self._setup_bluetooth()
        self._setup_networkmanager()
        self._setup_wpa_supplicant()
        self._setup_nfc()
        self._setup_socket_poller()
        self._setup_rfkill_poller()
        self._setup_rfcomm_poller()
        self._setup_usb_nic_poller()

    # -- Bluetooth (BlueZ) --------------------------------------------------

    def _setup_bluetooth(self):
        try:
            self.system_bus.add_signal_receiver(
                self._on_bt_interfaces_added,
                dbus_interface="org.freedesktop.DBus.ObjectManager",
                signal_name="InterfacesAdded",
                bus_name="org.bluez",
            )
            self.system_bus.add_signal_receiver(
                self._on_bt_interfaces_removed,
                dbus_interface="org.freedesktop.DBus.ObjectManager",
                signal_name="InterfacesRemoved",
                bus_name="org.bluez",
            )
            self.system_bus.add_signal_receiver(
                self._on_bt_properties_changed,
                dbus_interface="org.freedesktop.DBus.Properties",
                signal_name="PropertiesChanged",
                bus_name="org.bluez",
                path_keyword="path",
            )
            log("Bluetooth (BlueZ) monitor active.")
        except dbus.exceptions.DBusException as exc:
            log(f"BlueZ not available: {exc}", "WARN")

    def _on_bt_interfaces_added(self, path, interfaces):
        if "org.bluez.Device1" in interfaces:
            props = interfaces["org.bluez.Device1"]
            alias = str(props.get("Alias", "Unknown device"))
            addr = str(props.get("Address", "??:??"))
            ev = EventNormalizer.normalize(
                "bluetooth", "device_added", f"bt:add:{addr}",
                "Bluetooth device appeared",
                f"{alias} ({addr})",
                severity="warning",
            )
            self.dispatch(ev)

    def _on_bt_interfaces_removed(self, path, interfaces):
        if "org.bluez.Device1" in interfaces:
            addr = str(path).split("/")[-1].replace("_", ":")
            ev = EventNormalizer.normalize(
                "bluetooth", "device_removed", f"bt:rm:{addr}",
                "Bluetooth device removed",
                f"Path {path}",
            )
            self.dispatch(ev)

    def _on_bt_properties_changed(self, iface, changed, invalidated, path=""):
        if iface != "org.bluez.Device1":
            return
        if "Connected" in changed:
            connected = bool(changed["Connected"])
            addr = str(path).split("/")[-1].replace("_", ":")
            state = "connected" if connected else "disconnected"
            severity = "warning" if connected else "info"
            ev = EventNormalizer.normalize(
                "bluetooth", state, f"bt:{state}:{addr}",
                f"Bluetooth device {state}",
                f"Device {addr} {state}",
                severity=severity,
            )
            self.dispatch(ev)
        if "RSSI" in changed:
            addr = str(path).split("/")[-1].replace("_", ":")
            ev = EventNormalizer.normalize(
                "bluetooth", "rssi", f"bt:rssi:{addr}",
                "Bluetooth device nearby",
                f"Device {addr} RSSI={changed['RSSI']}",
            )
            self.dispatch(ev)

    # -- NetworkManager -----------------------------------------------------

    def _setup_networkmanager(self):
        try:
            self.system_bus.add_signal_receiver(
                self._on_nm_device_added,
                dbus_interface="org.freedesktop.NetworkManager",
                signal_name="DeviceAdded",
            )
            self.system_bus.add_signal_receiver(
                self._on_nm_device_removed,
                dbus_interface="org.freedesktop.NetworkManager",
                signal_name="DeviceRemoved",
            )
            self.system_bus.add_signal_receiver(
                self._on_nm_state_changed,
                dbus_interface="org.freedesktop.NetworkManager",
                signal_name="StateChanged",
            )
            self.system_bus.add_signal_receiver(
                self._on_nm_properties_changed,
                dbus_interface="org.freedesktop.DBus.Properties",
                signal_name="PropertiesChanged",
                bus_name="org.freedesktop.NetworkManager",
                path_keyword="path",
            )
            log("NetworkManager monitor active.")
        except dbus.exceptions.DBusException as exc:
            log(f"NetworkManager not available: {exc}", "WARN")

    _NM_STATES = {
        0: "Unknown", 10: "Asleep", 20: "Disconnected",
        30: "Disconnecting", 40: "Connecting", 50: "Connected-Local",
        60: "Connected-Site", 70: "Connected-Global",
    }

    def _on_nm_device_added(self, device_path):
        ev = EventNormalizer.normalize(
            "networkmanager", "device_added",
            f"nm:devadd:{device_path}",
            "Network device added",
            f"Device path: {device_path}",
            icon="network-wired",
        )
        self.dispatch(ev)

    def _on_nm_device_removed(self, device_path):
        ev = EventNormalizer.normalize(
            "networkmanager", "device_removed",
            f"nm:devrm:{device_path}",
            "Network device removed",
            f"Device path: {device_path}",
            icon="network-wired",
        )
        self.dispatch(ev)

    def _on_nm_state_changed(self, state):
        state_name = self._NM_STATES.get(int(state), str(state))
        ev = EventNormalizer.normalize(
            "networkmanager", "state_changed",
            f"nm:state:{state_name}",
            "Network state changed",
            f"NetworkManager → {state_name}",
            icon="network-wired",
        )
        self.dispatch(ev)

    def _on_nm_properties_changed(self, iface, changed, invalidated, path=""):
        interesting = {"Carrier", "ActiveAccessPoint", "State",
                       "Ip4Connectivity", "Ip6Connectivity"}
        for prop in interesting:
            if prop in changed:
                val = changed[prop]
                ev = EventNormalizer.normalize(
                    "networkmanager", "property",
                    f"nm:prop:{path}:{prop}",
                    f"NM property changed: {prop}",
                    f"{prop} = {val} on {path}",
                    icon="network-wired",
                )
                self.dispatch(ev)

    # -- wpa_supplicant -----------------------------------------------------

    def _setup_wpa_supplicant(self):
        try:
            self.system_bus.add_signal_receiver(
                self._on_wpa_properties_changed,
                dbus_interface="org.freedesktop.DBus.Properties",
                signal_name="PropertiesChanged",
                bus_name="fi.w1.wpa_supplicant1",
                path_keyword="path",
            )
            self.system_bus.add_signal_receiver(
                self._on_wpa_state_changed,
                dbus_interface="fi.w1.wpa_supplicant1.Interface",
                signal_name="StateChanged",
                path_keyword="path",
            )
            log("wpa_supplicant monitor active.")
        except dbus.exceptions.DBusException as exc:
            log(f"wpa_supplicant not available: {exc}", "WARN")

    def _on_wpa_properties_changed(self, iface, changed, invalidated, path=""):
        if "State" in changed:
            state = str(changed["State"])
            ev = EventNormalizer.normalize(
                "wpa_supplicant", "state",
                f"wpa:state:{path}:{state}",
                "Wi-Fi supplicant state",
                f"{path} → {state}",
                icon="network-wireless",
            )
            self.dispatch(ev)

    def _on_wpa_state_changed(self, new_state, old_state, path=""):
        ev = EventNormalizer.normalize(
            "wpa_supplicant", "state_changed",
            f"wpa:sc:{path}:{new_state}",
            "Wi-Fi state transition",
            f"{path}: {old_state} → {new_state}",
            icon="network-wireless",
        )
        self.dispatch(ev)

    # -- NFC (neard) --------------------------------------------------------

    def _setup_nfc(self):
        try:
            self.system_bus.add_signal_receiver(
                self._on_nfc_interfaces_added,
                dbus_interface="org.freedesktop.DBus.ObjectManager",
                signal_name="InterfacesAdded",
                bus_name="org.neard",
            )
            self.system_bus.add_signal_receiver(
                self._on_nfc_interfaces_removed,
                dbus_interface="org.freedesktop.DBus.ObjectManager",
                signal_name="InterfacesRemoved",
                bus_name="org.neard",
            )
            self.system_bus.add_signal_receiver(
                self._on_nfc_properties_changed,
                dbus_interface="org.freedesktop.DBus.Properties",
                signal_name="PropertiesChanged",
                bus_name="org.neard",
                path_keyword="path",
            )
            log("NFC (neard) monitor active.")
        except dbus.exceptions.DBusException as exc:
            log(f"neard not available (NFC disabled): {exc}", "DEBUG")

    def _on_nfc_interfaces_added(self, path, interfaces):
        ev = EventNormalizer.normalize(
            "nfc", "added", f"nfc:add:{path}",
            "NFC device/tag detected",
            f"Path: {path}",
        )
        self.dispatch(ev)

    def _on_nfc_interfaces_removed(self, path, interfaces):
        ev = EventNormalizer.normalize(
            "nfc", "removed", f"nfc:rm:{path}",
            "NFC device/tag removed",
            f"Path: {path}",
        )
        self.dispatch(ev)

    def _on_nfc_properties_changed(self, iface, changed, invalidated, path=""):
        for prop, val in changed.items():
            ev = EventNormalizer.normalize(
                "nfc", "property", f"nfc:prop:{path}:{prop}",
                f"NFC property: {prop}",
                f"{prop} = {val}",
            )
            self.dispatch(ev)

    # -- Inbound socket poller (ss) -----------------------------------------

    def _setup_socket_poller(self):
        self._known_connections = set()
        self._listening_ports = set()
        GLib.timeout_add_seconds(2, self._poll_sockets)
        log("Inbound socket poller active (2 s interval).")

    @staticmethod
    def _classify_ip(host):
        normalized = host.strip("[]")
        try:
            ip = ipaddress.ip_address(normalized)
        except ValueError:
            return "hostname or unresolved address"

        family = "IPv6" if ip.version == 6 else "IPv4"
        if ip.is_loopback:
            scope = "loopback"
        elif ip.is_link_local:
            scope = "link-local"
        elif ip.is_private:
            scope = "private"
        elif ip.is_multicast:
            scope = "multicast"
        elif ip.is_reserved:
            scope = "reserved"
        elif ip.is_unspecified:
            scope = "unspecified"
        else:
            scope = "public"
        return f"{scope} {family}"

    @staticmethod
    def _port_label(proto, port):
        proto_name = "udp" if proto.startswith("udp") else "tcp"
        try:
            service = socket.getservbyport(port, proto_name)
        except OSError:
            return str(port)
        return f"{port} ({service})"

    @staticmethod
    def _extract_process_info(process_field):
        if not process_field:
            return None, None

        name_match = re.search(r'"([^"]+)"', process_field)
        pid_match = re.search(r"pid=(\d+)", process_field)
        proc_name = name_match.group(1) if name_match else None
        pid = pid_match.group(1) if pid_match else None
        return proc_name, pid

    def _format_socket_body(self, proto, lhost, lport, rhost, rport, process_field=""):
        peer_type = self._classify_ip(rhost)
        local_label = self._port_label(proto, lport)
        remote_label = self._port_label(proto, rport)
        proc_name, pid = self._extract_process_info(process_field)
        lines = [
            f"Visible peer: {rhost}:{remote_label}",
            f"Peer type: {peer_type}",
            f"Local endpoint: {lhost}:{local_label}",
        ]
        if proc_name and pid:
            lines.append(f"Target process: {proc_name} (pid {pid})")
        elif proc_name:
            lines.append(f"Target process: {proc_name}")
        else:
            lines.append("Target process: unavailable from ss")
        lines.append(
            "Origin note: last visible hop only; NAT, VPN, proxy, relay, or tunnel may hide the original device."
        )
        return "\n".join(lines)

    @staticmethod
    def _parse_ss_lines(output):
        """Parse ss output lines into (proto, local_addr, local_port,
        remote_addr, remote_port) tuples."""
        results = []
        for line in output.strip().splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            proto = parts[0]
            local = parts[3]
            remote = parts[4]
            lhost, _, lport = local.rpartition(":")
            rhost, _, rport = remote.rpartition(":")
            try:
                lport_i = int(lport)
                rport_i = int(rport)
            except ValueError:
                continue
            process_field = " ".join(parts[5:]) if len(parts) > 5 else ""
            results.append((proto, lhost, lport_i, rhost, rport_i, process_field))
        return results

    @staticmethod
    def _is_noisy(proto, lhost, lport, rhost, rport):
        if lhost in ("127.0.0.1", "::1", "[::1]"):
            return True
        if rhost in ("127.0.0.1", "::1", "[::1]"):
            return True
        # link-local
        if rhost.startswith("169.254.") or rhost.startswith("fe80"):
            return True
        if proto == "udp" and lport in NOISY_UDP_PORTS:
            return True
        if proto == "udp" and rport in NOISY_UDP_PORTS:
            return True
        return False

    def _poll_sockets(self):
        try:
            listen_out = subprocess.check_output(
                ["ss", "-Hltnup"], text=True, timeout=5,
                stderr=subprocess.DEVNULL,
            )
            self._listening_ports = set()
            for entry in self._parse_ss_lines(listen_out):
                self._listening_ports.add((entry[0], entry[2]))

            conn_out = subprocess.check_output(
                ["ss", "-Htnup"], text=True, timeout=5,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            log(f"ss poll failed: {exc}", "WARN")
            return True

        current = set()
        for proto, lhost, lport, rhost, rport, process_field in self._parse_ss_lines(conn_out):
            if (proto, lport) not in self._listening_ports:
                continue
            if self._is_noisy(proto, lhost, lport, rhost, rport):
                continue
            conn_key = (proto, lhost, lport, rhost, rport, process_field)
            current.add(conn_key)

        new_connections = current - self._known_connections
        self._known_connections = current

        for proto, lhost, lport, rhost, rport, process_field in new_connections:
            body = self._format_socket_body(
                proto, lhost, lport, rhost, rport, process_field
            )
            ev = EventNormalizer.normalize(
                "socket", "inbound",
                f"sock:{proto}:{rhost}:{rport}->{lhost}:{lport}",
                f"Inbound {proto.upper()} connection",
                body,
                icon="network-server",
                severity="warning",
            )
            log(f"Socket details: {body.replace(chr(10), ' | ')}")
            self.dispatch(ev)

        return True  # keep polling

    # -- rfkill poller ------------------------------------------------------

    def _setup_rfkill_poller(self):
        self._rfkill_state = {}
        GLib.timeout_add_seconds(5, self._poll_rfkill)
        log("rfkill poller active (5 s interval).")

    def _poll_rfkill(self):
        rfkill_base = "/sys/class/rfkill"
        try:
            entries = os.listdir(rfkill_base)
        except OSError:
            return True

        for entry in entries:
            path = os.path.join(rfkill_base, entry)
            try:
                with open(os.path.join(path, "type")) as f:
                    rtype = f.read().strip()
                with open(os.path.join(path, "soft")) as f:
                    soft = f.read().strip()
                with open(os.path.join(path, "hard")) as f:
                    hard = f.read().strip()
            except OSError:
                continue

            state_key = f"rfkill:{entry}"
            current = (rtype, soft, hard)
            prev = self._rfkill_state.get(state_key)
            self._rfkill_state[state_key] = current

            if prev is not None and prev != current:
                blocked = soft == "1" or hard == "1"
                status = "BLOCKED" if blocked else "unblocked"
                sev = "warning" if blocked else "info"
                ev = EventNormalizer.normalize(
                    "rfkill", "change",
                    f"rfkill:{entry}:{status}",
                    f"Radio {status}: {rtype}",
                    f"{entry} ({rtype}) soft={soft} hard={hard}",
                    icon="dialog-warning" if blocked else ICON_MAP.get(rtype, "dialog-information"),
                    severity=sev,
                )
                self.dispatch(ev)

        return True

    # -- RFCOMM poller ------------------------------------------------------

    def _setup_rfcomm_poller(self):
        self._known_rfcomm = set()
        GLib.timeout_add_seconds(5, self._poll_rfcomm)
        log("RFCOMM poller active (5 s interval).")

    def _poll_rfcomm(self):
        current = set(glob.glob("/dev/rfcomm*"))
        added = current - self._known_rfcomm
        removed = self._known_rfcomm - current
        self._known_rfcomm = current

        for dev in added:
            ev = EventNormalizer.normalize(
                "rfcomm", "added", f"rfcomm:add:{dev}",
                "RFCOMM device appeared",
                f"{dev}",
                icon="bluetooth-active",
                severity="warning",
            )
            self.dispatch(ev)
        for dev in removed:
            ev = EventNormalizer.normalize(
                "rfcomm", "removed", f"rfcomm:rm:{dev}",
                "RFCOMM device removed",
                f"{dev}",
                icon="bluetooth-active",
            )
            self.dispatch(ev)

        return True

    # -- USB NIC poller -----------------------------------------------------

    def _setup_usb_nic_poller(self):
        self._known_usb_nics = set()
        GLib.timeout_add_seconds(5, self._poll_usb_nics)
        log("USB NIC poller active (5 s interval).")

    def _poll_usb_nics(self):
        current = set()
        net_base = "/sys/class/net"
        try:
            ifaces = os.listdir(net_base)
        except OSError:
            return True

        for iface in ifaces:
            device_link = os.path.join(net_base, iface, "device")
            if not os.path.islink(device_link):
                continue
            target = os.path.realpath(device_link)
            if "/usb" in target:
                current.add(iface)

        added = current - self._known_usb_nics
        removed = self._known_usb_nics - current
        self._known_usb_nics = current

        for iface in added:
            ev = EventNormalizer.normalize(
                "usb_nic", "added", f"usbnic:add:{iface}",
                "USB network adapter detected",
                f"Interface: {iface}",
                icon="drive-removable-media-usb",
                severity="warning",
            )
            self.dispatch(ev)
        for iface in removed:
            ev = EventNormalizer.normalize(
                "usb_nic", "removed", f"usbnic:rm:{iface}",
                "USB network adapter removed",
                f"Interface: {iface}",
                icon="drive-removable-media-usb",
            )
            self.dispatch(ev)

        return True

    # -- run ----------------------------------------------------------------

    def run(self):
        self.setup()
        log(f"Daemon started (PID {os.getpid()}). "
            f"Warmup for {WARMUP_SECONDS}s — baselining existing state.")
        GLib.timeout_add_seconds(60, self._prune_queue)

        def _quit(signum, _frame):
            sig_name = signal.Signals(signum).name
            log(f"Received {sig_name}, shutting down.")
            self.loop.quit()

        signal.signal(signal.SIGINT, _quit)
        signal.signal(signal.SIGTERM, _quit)

        try:
            self.loop.run()
        except KeyboardInterrupt:
            pass
        finally:
            log("Daemon stopped.")

    @staticmethod
    def _prune_queue():
        EventQueuePublisher.prune()
        return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log("connot_daemon starting…")
    daemon = ConnNotifyDaemon()
    daemon.run()


if __name__ == "__main__":
    main()
