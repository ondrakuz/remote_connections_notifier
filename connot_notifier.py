#!/usr/bin/env python3
"""
connot_notifier.py — User-side KDE notifier for Connection Notifier.

Reads normalized events emitted by the root collector from /run/connot/events
and sends desktop notifications in the current user session.
"""

import json
import os
import signal
import subprocess
import sys
import time


QUEUE_DIR = "/run/connot/events"
POLL_SECONDS = 1.0


def log(msg, level="INFO"):
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{ts}] [{level}] CONNOT-NOTIFIER: {msg}", file=sys.stderr, flush=True)


class NotificationManager:
    @staticmethod
    def send(title, body, icon="dialog-information", delivery=None):
        delivery = delivery or {}
        args = ["notify-send", "-a", "Connection Notifier", "-i", icon]
        args.extend(["-u", delivery.get("urgency", "normal")])
        args.extend(["-t", str(delivery.get("expire_ms", 5000))])
        category = delivery.get("category")
        if category:
            args.extend(["-c", category])
        if delivery.get("transient", False):
            args.append("--transient")
        args.extend([title, body])
        try:
            subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            try:
                subprocess.Popen(
                    ["kdialog", "--passivepopup", f"{title}\n{body}", "8"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                log(f"No notification backend available: {title}: {body}", "WARN")


class EventConsumer:
    def __init__(self):
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        self.state_file = os.path.join(runtime_dir, "connot_notifier.last")
        self.last_seen = self._load_last_seen()
        self._running = True

    def _load_last_seen(self):
        try:
            with open(self.state_file, encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""

    def _save_last_seen(self, value):
        try:
            with open(self.state_file, "w", encoding="utf-8") as handle:
                handle.write(value)
        except OSError as exc:
            log(f"Failed to persist notifier state: {exc}", "WARN")

    def _list_event_files(self):
        try:
            return sorted(
                entry for entry in os.listdir(QUEUE_DIR) if entry.endswith(".json")
            )
        except OSError:
            return []

    def baseline_existing(self):
        existing = self._list_event_files()
        if not existing:
            return
        self.last_seen = existing[-1]
        self._save_last_seen(self.last_seen)
        log(f"Baselined existing queue at {self.last_seen}")

    def _iter_new_files(self):
        for entry in self._list_event_files():
            if self.last_seen and entry <= self.last_seen:
                continue
            yield entry

    def _load_event(self, filename):
        path = os.path.join(QUEUE_DIR, filename)
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            log(f"Failed to read event {filename}: {exc}", "WARN")
            return None

    def process_pending(self):
        for filename in self._iter_new_files():
            event = self._load_event(filename)
            self.last_seen = filename
            self._save_last_seen(filename)
            if not event:
                continue
            NotificationManager.send(
                event.get("title", "Connection Notifier event"),
                event.get("body", ""),
                event.get("icon", "dialog-information"),
                event.get("delivery", {}),
            )
            delivery = event.get("delivery", {})
            log(
                f"DELIVERED severity={event.get('severity', 'info')} "
                f"delivery={'persistent' if delivery.get('persistent') else 'transient'} "
                f"title={event.get('title', 'Connection Notifier event')!r} "
                f"body={event.get('body', '')!r}"
            )

    def run(self):
        self.baseline_existing()
        log(f"Notifier started (PID {os.getpid()}). Watching {QUEUE_DIR}")

        def _quit(signum, _frame):
            sig_name = signal.Signals(signum).name
            log(f"Received {sig_name}, shutting down.")
            self._running = False

        signal.signal(signal.SIGINT, _quit)
        signal.signal(signal.SIGTERM, _quit)

        while self._running:
            self.process_pending()
            time.sleep(POLL_SECONDS)

        log("Notifier stopped.")


def main():
    log("connot_notifier starting…")
    EventConsumer().run()


if __name__ == "__main__":
    main()
