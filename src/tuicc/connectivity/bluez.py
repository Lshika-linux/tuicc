"""bluez backend — talks to bluez directly over D-Bus (org.bluez)
rather than shelling out to bluetoothctl. Mirrors iwd.py's shape:
short-lived io.blocking connections (one per call), a pure
objects-dict-parsing function kept separate from the D-Bus I/O for
testability, no internal try/except hiding a real failure behind an
empty list (VISION.md's R3 — the bluetoothctl-based version this
replaces already learned that lesson once).

Migrated off bluetoothctl for R4: `bluetoothctl devices Paired` only
ever lists already-paired devices, so a brand-new device could never
be discovered or paired from inside tuicc. GetManagedObjects() walks
every org.bluez.Device1 object bluez currently knows about — paired or
not — and Device1.Pair() (which this backend's connect() now calls
first for an unpaired device) is what actually drives the BluezAgent
registered in bluez_agent.py, the same one-keypress "Connect() just
works, an agent handles the interactive part" shape iwd.py already has
for unknown wifi networks.
"""

import threading

from jeepney.io.blocking import open_dbus_connection

from tuicc.connectivity.base import BluetoothBackend
from tuicc.connectivity.model import BluetoothDevice
from tuicc.connectivity.util import dbus_call

BUS_NAME = "org.bluez"

# Device1.Pair() blocks until the full BluezAgent round-trip resolves:
# bluez calls our registered agent's RequestConfirmation/
# RequestAuthorization (which publishes an AgentMailbox request and
# returns immediately — see agent_mailbox.py's own module docstring),
# and bluez's Pair() call itself only gets its reply once the user
# actually answers, all the way over in main.py's "connectivity_pairing"
# input_claim tier — same reasoning as iwd.py's CONNECT_TIMEOUT_SECONDS.
# If the user walks away, bluez's own agent request timeout eventually
# cancels it from bluez's side, sending our agent Cancel — BluezAgent's
# dispatch loop notices that immediately (it never blocks waiting on
# us), so this Pair() call fails with a real bluez error long before
# this deadline matters.
PAIR_TIMEOUT_SECONDS = 120

# Device1.Connect() on an already-paired device is a plain (non-
# interactive) operation, but real devices can be slow to accept a
# connection — more headroom than a plain property read, not the full
# interactive PAIR_TIMEOUT_SECONDS.
CONNECT_TIMEOUT_SECONDS = 30

# How long one StartDiscovery() window stays open — see
# start_discovery()'s own docstring for why this needs a dedicated
# background thread at all, not just a plain D-Bus call.
DISCOVERY_WINDOW_SECONDS = 15


def _call(connection, path, interface, member, signature="", body=(), timeout=5):
    return dbus_call(connection, BUS_NAME, path, interface, member, signature, body, timeout)


def find_devices_in_objects(objects):
    """Pure logic half of get_devices(): given the dict returned by
    ObjectManager.GetManagedObjects (path -> {interface: props}),
    return one BluetoothDevice per object implementing
    org.bluez.Device1 — paired or not, per this module's own docstring.
    Testable without any D-Bus connection.
    """
    devices = []
    for interfaces in objects.values():
        device_props = interfaces.get("org.bluez.Device1")
        if device_props is None:
            continue
        devices.append(_device_from_props(device_props, interfaces.get("org.bluez.Battery1")))
    return devices


def _device_from_props(device_props, battery_props):
    # Alias is always present (bluez defaults it to Name, or the
    # address itself if neither the device nor the user ever set one)
    # — Name is optional (only present once bluez has actually learned
    # it from the device), so Alias is the reliable one to display.
    name = device_props["Alias"][1]
    battery = battery_props["Percentage"][1] if battery_props is not None else None
    # Icon/RSSI are genuinely optional in bluez's own reply (Icon: only
    # once bluez has a class-based guess; RSSI: only while the device
    # is actively visible in a scan) — .get() rather than [...], same
    # "absent means unknown, not a KeyError" handling every other
    # optional D-Bus property in this codebase gets.
    icon_prop = device_props.get("Icon")
    address_type_prop = device_props.get("AddressType")
    rssi_prop = device_props.get("RSSI")
    return BluetoothDevice(
        id=device_props["Address"][1],
        name=name,
        connected=device_props["Connected"][1],
        battery=battery,
        paired=device_props["Paired"][1],
        trusted=device_props["Trusted"][1],
        blocked=device_props["Blocked"][1],
        icon=icon_prop[1] if icon_prop is not None else None,
        address_type=address_type_prop[1] if address_type_prop is not None else None,
        rssi=rssi_prop[1] if rssi_prop is not None else None,
    )


def find_device_path_in_objects(objects, device_id):
    """The object path of the Device1 object whose Address matches
    device_id (a MAC address) — device paths are derived from the
    owning adapter's own path (which isn't guaranteed to be `hci0`),
    so this walks the tree rather than assuming a fixed prefix, same
    reasoning as iwd.py's find_station_path_in_objects.
    """
    for path, interfaces in objects.items():
        device_props = interfaces.get("org.bluez.Device1")
        if device_props is not None and device_props["Address"][1] == device_id:
            return path
    return None


def find_adapter_path_in_objects(objects):
    for path, interfaces in objects.items():
        if "org.bluez.Adapter1" in interfaces:
            return path
    return None


def _get_managed_objects(connection):
    return _call(connection, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")[0]


class BluezBackend(BluetoothBackend):
    def __init__(self):
        # Set only while a discovery window (see start_discovery()) is
        # currently running — lets stop_discovery() cut it short, and
        # lets a redundant start_discovery() while one's already
        # running be a no-op instead of opening a second, overlapping
        # StartDiscovery() session.
        self._discovery_stop = None

    def get_devices(self) -> list[BluetoothDevice]:
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            return find_devices_in_objects(_get_managed_objects(connection))
        finally:
            connection.close()

    def connect(self, device_id: str) -> None:
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            objects = _get_managed_objects(connection)
            device_path = find_device_path_in_objects(objects, device_id)
            if device_path is None:
                return
            device_props = objects[device_path]["org.bluez.Device1"]
            if not device_props["Paired"][1]:
                # See PAIR_TIMEOUT_SECONDS' own comment — an unpaired
                # device's Pair() blocks on the full BluezAgent
                # round-trip, not just a plain D-Bus call.
                _call(connection, device_path, "org.bluez.Device1", "Pair", timeout=PAIR_TIMEOUT_SECONDS)
            _call(connection, device_path, "org.bluez.Device1", "Connect", timeout=CONNECT_TIMEOUT_SECONDS)
        finally:
            connection.close()

    def disconnect(self, device_id: str) -> None:
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            device_path = find_device_path_in_objects(_get_managed_objects(connection), device_id)
            if device_path:
                _call(connection, device_path, "org.bluez.Device1", "Disconnect")
        finally:
            connection.close()

    def start_discovery(self) -> None:
        """Runs StartDiscovery -> wait -> StopDiscovery on its own
        background thread with a held-open connection, unlike every
        other method here — see
        CLAUDE/NOTES/design-decisions.md#bluez-discovery-connection for
        why that's required, not a style choice. No-op if a discovery
        window is already running.
        """
        if self._discovery_stop is not None:
            return
        stop_event = threading.Event()
        self._discovery_stop = stop_event
        threading.Thread(target=self._run_discovery_window, args=(stop_event,), daemon=True).start()

    def _run_discovery_window(self, stop_event):
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            adapter_path = find_adapter_path_in_objects(_get_managed_objects(connection))
            if adapter_path is not None:
                _call(connection, adapter_path, "org.bluez.Adapter1", "StartDiscovery")
                # Ends early if stop_discovery() sets the event, else
                # once the window elapses — either way StopDiscovery
                # still runs right after, same as the timeout path.
                stop_event.wait(timeout=DISCOVERY_WINDOW_SECONDS)
                _call(connection, adapter_path, "org.bluez.Adapter1", "StopDiscovery")
        finally:
            connection.close()
            self._discovery_stop = None

    def stop_discovery(self) -> None:
        if self._discovery_stop is not None:
            self._discovery_stop.set()

    def is_discovering(self) -> bool:
        """Adapter1.Discovering — the real ground truth, not this
        backend's own _discovery_stop tracking (which wouldn't know
        about discovery started by bluetoothctl or another app).
        Polled as its own StatusWorker Domain, same reason
        IwdBackend.is_scanning() is: is_pending("bluetooth", ...) alone
        clears almost immediately since start_discovery() itself
        returns fast, which would make the "Discovering…" label flicker.
        """
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            adapter_path = find_adapter_path_in_objects(_get_managed_objects(connection))
            if adapter_path is None:
                return False
            props = _call(connection, adapter_path, "org.freedesktop.DBus.Properties", "GetAll",
                           "s", ("org.bluez.Adapter1",))[0]
            discovering_prop = props.get("Discovering")
            return discovering_prop[1] if discovering_prop is not None else False
        finally:
            connection.close()
