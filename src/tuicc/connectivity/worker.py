"""Background polling for wifi/bluetooth state, so the main render
loop never blocks on a D-Bus round-trip or a slow connect() call.

Mirrors swcc's daemon architecture (a background worker feeding cached
state to the render loop) but as a thread within the same process,
not a separate one over a Unix socket — tuicc is single-process, so
there's no need for IPC here, just a lock-protected shared state.
"""

import threading
import time


class ConnectivityWorker:
    def __init__(self, wifi_backend, bluetooth_backend, poll_interval=5):
        self._wifi_backend = wifi_backend
        self._bluetooth_backend = bluetooth_backend
        self._poll_interval = poll_interval

        self._lock = threading.Lock()
        self._wifi_networks = []
        self._bluetooth_devices = []

        self._action_queue = []
        self._pending = set()  # {("wifi", ssid), ("bt", device_id)}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def get_wifi_networks(self):
        with self._lock:
            return list(self._wifi_networks)

    def get_bluetooth_devices(self):
        with self._lock:
            return list(self._bluetooth_devices)

    def is_wifi_pending(self, ssid):
        with self._lock:
            return ("wifi", ssid) in self._pending

    def is_bluetooth_pending(self, device_id):
        with self._lock:
            return ("bt", device_id) in self._pending

    def request_wifi_connect(self, ssid):
        with self._lock:
            self._action_queue.append(("wifi_connect", ssid))
            self._pending.add(("wifi", ssid))

    def request_wifi_disconnect(self, ssid):
        with self._lock:
            self._action_queue.append(("wifi_disconnect", ssid))
            self._pending.add(("wifi", ssid))

    def request_bluetooth_connect(self, device_id):
        with self._lock:
            self._action_queue.append(("bt_connect", device_id))
            self._pending.add(("bt", device_id))

    def request_bluetooth_disconnect(self, device_id):
        with self._lock:
            self._action_queue.append(("bt_disconnect", device_id))
            self._pending.add(("bt", device_id))

    def has_pending(self):
            with self._lock:
                return len(self._pending) > 0

    def _run(self):
        last_poll = 0
        while not self._stop.is_set():
            with self._lock:
                actions = self._action_queue[:]
                self._action_queue.clear()

            for kind, arg in actions:
                try:
                    if kind == "wifi_connect":
                        self._wifi_backend.connect(arg)
                    elif kind == "wifi_disconnect":
                        self._wifi_backend.disconnect()
                    elif kind == "bt_connect":
                        self._bluetooth_backend.connect(arg)
                    elif kind == "bt_disconnect":
                        self._bluetooth_backend.disconnect(arg)
                except Exception:
                    pass
                finally:
                    with self._lock:
                        if kind in ("wifi_connect", "wifi_disconnect"):
                            self._pending.discard(("wifi", arg))
                        elif kind in ("bt_connect", "bt_disconnect"):
                            self._pending.discard(("bt", arg))

            now = time.monotonic()
            if actions or now - last_poll > self._poll_interval:
                try:
                    wifi = self._wifi_backend.get_networks()
                except Exception:
                    wifi = []
                try:
                    bt = self._bluetooth_backend.get_devices()
                except Exception:
                    bt = []

                with self._lock:
                    self._wifi_networks = wifi
                    self._bluetooth_devices = bt
                last_poll = now

            time.sleep(0.2)
