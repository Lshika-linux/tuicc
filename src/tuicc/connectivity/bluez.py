"""bluez backend — translates bluetoothctl CLI output into tuicc's
bluetooth model. Same pure-parsing-function pattern as iwd.py.
"""

import re
import subprocess

from tuicc.connectivity.base import BluetoothBackend
from tuicc.connectivity.model import BluetoothDevice
from tuicc.connectivity.util import strip_ansi

def _run(cmd, timeout=5):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return strip_ansi(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def parse_paired_devices_output(text):
    """`bluetoothctl devices Paired` output -> list of (mac, name)."""
    devices = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("Device "):
            continue
        parts = line.split(" ", 2)
        if len(parts) == 3:
            _label, mac, name = parts
            devices.append((mac, name))
    return devices


_BATTERY_RE = re.compile(r"Battery Percentage:.*\((\d+)\)")


def parse_info_output(text):
    """`bluetoothctl info <mac>` output -> dict with 'connected' (bool)
    and 'battery' (int or None).
    """
    connected = False
    battery = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Connected:"):
            connected = line.split(":", 1)[1].strip().lower() == "yes"
        else:
            match = _BATTERY_RE.search(line)
            if match:
                battery = int(match.group(1))
    return {"connected": connected, "battery": battery}


class BluezBackend(BluetoothBackend):
    def get_devices(self) -> list[BluetoothDevice]:
        paired = parse_paired_devices_output(_run(["bluetoothctl", "devices", "Paired"]))
        devices = []
        for mac, name in paired:
            info = parse_info_output(_run(["bluetoothctl", "info", mac]))
            devices.append(BluetoothDevice(
                id=mac,
                name=name,
                connected=info["connected"],
                battery=info["battery"],
            ))
        return devices

    def connect(self, device_id: str) -> None:
        _run(["bluetoothctl", "connect", device_id], timeout=15)

    def disconnect(self, device_id: str) -> None:
        _run(["bluetoothctl", "disconnect", device_id])
