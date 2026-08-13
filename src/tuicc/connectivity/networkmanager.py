"""NetworkManager backend — talks to NetworkManager directly over
D-Bus (org.freedesktop.NetworkManager), the second WifiBackend
alongside iwd.py's, for users who don't run iwd.

Real structural differences from iwd.py (no bulk ObjectManager tree
walk, per-BSSID not per-SSID access points, no built-in scan-result-to-
saved-profile link, genuinely async ActivateConnection needing a real
poll loop) are documented in
CLAUDE/NOTES/design-decisions.md#networkmanager-vs-iwd — read that
before touching this file.

NOT YET CONFIRMED against a real, running NetworkManager (built on a
machine that runs iwd) — see that same note for what's fixture-tested
vs. genuinely unverified.
"""

import time
from datetime import datetime, timezone

from jeepney.io.blocking import open_dbus_connection

from tuicc.connectivity.base import WifiBackend
from tuicc.connectivity.model import WifiNetwork
from tuicc.connectivity.util import dbus_call, decode_ssid

BUS_NAME = "org.freedesktop.NetworkManager"
ROOT_PATH = "/org/freedesktop/NetworkManager"
SETTINGS_PATH = "/org/freedesktop/NetworkManager/Settings"
IFACE_ROOT = "org.freedesktop.NetworkManager"
IFACE_DEVICE = "org.freedesktop.NetworkManager.Device"
IFACE_WIRELESS = "org.freedesktop.NetworkManager.Device.Wireless"
IFACE_AP = "org.freedesktop.NetworkManager.AccessPoint"
IFACE_SETTINGS = "org.freedesktop.NetworkManager.Settings"
IFACE_CONNECTION = "org.freedesktop.NetworkManager.Settings.Connection"
IFACE_ACTIVE_CONNECTION = "org.freedesktop.NetworkManager.Connection.Active"
IFACE_PROPERTIES = "org.freedesktop.DBus.Properties"

NM_DEVICE_TYPE_WIFI = 2

# NM80211ApFlags (nm-dbus-types.html)
NM_AP_FLAG_PRIVACY = 0x1

# NM80211ApSecurityFlags — same bit values apply to both WpaFlags and RsnFlags
NM_AP_SEC_KEY_MGMT_PSK = 0x100
NM_AP_SEC_KEY_MGMT_802_1X = 0x200
NM_AP_SEC_KEY_MGMT_SAE = 0x400  # WPA3-Personal
NM_AP_SEC_KEY_MGMT_OWE = 0x800
NM_AP_SEC_KEY_MGMT_OWE_TM = 0x1000
NM_AP_SEC_KEY_MGMT_EAP_SUITE_B_192 = 0x2000

# NMActiveConnectionState
NM_ACTIVE_CONNECTION_STATE_ACTIVATING = 1
NM_ACTIVE_CONNECTION_STATE_ACTIVATED = 2
NM_ACTIVE_CONNECTION_STATE_DEACTIVATING = 3
NM_ACTIVE_CONNECTION_STATE_DEACTIVATED = 4

# NMDeviceStateReason (the wifi-auth-relevant subset)
NM_DEVICE_STATE_REASON_NO_SECRETS = 7
NM_DEVICE_STATE_REASON_SUPPLICANT_DISCONNECT = 8
NM_DEVICE_STATE_REASON_SUPPLICANT_CONFIG_FAILED = 9
NM_DEVICE_STATE_REASON_SUPPLICANT_FAILED = 10
NM_DEVICE_STATE_REASON_SUPPLICANT_TIMEOUT = 11
NM_DEVICE_STATE_REASON_SSID_NOT_FOUND = 53

_FAILURE_REASON_MESSAGES = {
    NM_DEVICE_STATE_REASON_NO_SECRETS: "wrong passphrase (no valid secrets)",
    NM_DEVICE_STATE_REASON_SUPPLICANT_DISCONNECT: "disconnected during authentication (often a wrong passphrase)",
    NM_DEVICE_STATE_REASON_SUPPLICANT_CONFIG_FAILED: "wpa_supplicant configuration failed",
    NM_DEVICE_STATE_REASON_SUPPLICANT_FAILED: "wpa_supplicant failed",
    NM_DEVICE_STATE_REASON_SUPPLICANT_TIMEOUT: "authentication timed out",
    NM_DEVICE_STATE_REASON_SSID_NOT_FOUND: "network not found",
}

# See connect()'s own docstring / _wait_for_activation() — mirrors
# iwd.py's own CONNECT_TIMEOUT_SECONDS and its exact reasoning: the
# real long pole is the registered NetworkManagerAgent's GetSecrets
# round-trip waiting on the user, not NetworkManager's own activation
# machinery.
CONNECT_TIMEOUT_SECONDS = 120
CONNECT_POLL_INTERVAL_SECONDS = 0.5

# See is_scanning()'s own docstring — how long a scan can run before
# this backend gives up waiting for LastScan to move (RequestScan()
# can silently no-op — rfkill, a busy device — with no error of its
# own to catch).
SCAN_TIMEOUT_SECONDS = 15


def _call(connection, path, interface, member, signature="", body=(), timeout=5):
    return dbus_call(connection, BUS_NAME, path, interface, member, signature, body, timeout)


def find_wifi_device_path(device_types):
    """Pure logic: given `[(path, device_type_int), ...]` — one entry
    per device path from `NetworkManager.GetAllDevices()`, each paired
    with its own `Device.DeviceType` property (a separate
    `Properties.Get` call per device — see this module's own docstring
    for why there's no bulk tree to walk the way iwd.py's
    `find_station_path_in_objects()` gets) — the first path whose type
    is `NM_DEVICE_TYPE_WIFI`, or None. Testable without any D-Bus
    connection.
    """
    for path, device_type in device_types:
        if device_type == NM_DEVICE_TYPE_WIFI:
            return path
    return None


def classify_security(flags, wpa_flags, rsn_flags):
    """Pure logic: NM80211ApFlags/NM80211ApSecurityFlags bitmasks ->
    the same security-token vocabulary iwd.py's own `Network.Type`
    already uses where the concept is identical ("open"/"psk"/"8021x"),
    extended with tokens iwd has no equivalent of ("wep"/"sae"/"owe").

    WPA2/WPA3 "transition mode" APs advertise BOTH `KEY_MGMT_PSK` and
    `KEY_MGMT_SAE` in `RsnFlags` simultaneously — `"psk"` wins
    deliberately: a transition-mode AP is PSK-compatible without
    needing any extra client-side SAE assumption, so treating it as
    "sae" would be the more restrictive (and less useful) read.
    """
    combined = wpa_flags | rsn_flags
    if combined & NM_AP_SEC_KEY_MGMT_SAE:
        if combined & NM_AP_SEC_KEY_MGMT_PSK:
            return "psk"
        return "sae"
    if combined & (NM_AP_SEC_KEY_MGMT_802_1X | NM_AP_SEC_KEY_MGMT_EAP_SUITE_B_192):
        return "8021x"
    if combined & NM_AP_SEC_KEY_MGMT_PSK:
        return "psk"
    if combined & (NM_AP_SEC_KEY_MGMT_OWE | NM_AP_SEC_KEY_MGMT_OWE_TM):
        return "owe"
    if flags & NM_AP_FLAG_PRIVACY:
        return "wep"
    return "open"


def merge_access_points_by_ssid(entries):
    """Pure logic: the per-BSSID-vs-per-SSID model mismatch this
    module's docstring describes. `entries` is a flat list of decoded
    `{"ssid", "strength", "security", "path", "connected"}` dicts, one
    per real `AccessPoint`. Groups by `ssid`; strongest `strength` wins
    as the group's representative, but `connected` is ORed across the
    whole group. Entries with an empty decoded ssid (fully hidden AP)
    are excluded, same as iwd.py. Testable without a D-Bus connection.
    """
    groups = {}
    for entry in entries:
        ssid = entry["ssid"]
        if not ssid:
            continue
        groups.setdefault(ssid, []).append(entry)

    merged = []
    for ssid, group in groups.items():
        strongest = max(group, key=lambda e: e["strength"])
        merged.append({
            "ssid": ssid,
            "strength": strongest["strength"],
            "security": strongest["security"],
            "path": strongest["path"],
            "connected": any(e["connected"] for e in group),
        })
    return merged


def find_known_profile_for_ssid(connections, ssid):
    """Pure logic: given `[(path, settings_dict), ...]` from
    `Settings.ListConnections()` + per-connection `GetSettings()`, the
    first connection path whose `802-11-wireless.ssid` (a byte array,
    decoded via `util.decode_ssid`) matches `ssid`, or None. Profiles
    missing the `802-11-wireless` setting entirely (VPNs, ethernet,
    ...) are skipped safely — there is no built-in link from a scanned
    AccessPoint to a profile the way iwd's `Network.KnownNetwork`
    property provides, so this correlation has to happen here.
    Testable without any D-Bus connection.
    """
    for path, settings in connections:
        wireless = settings.get("802-11-wireless")
        if wireless is None:
            continue
        ssid_prop = wireless.get("ssid")
        if ssid_prop is None:
            continue
        if decode_ssid(ssid_prop[1]) == ssid:
            return path
    return None


def known_profile_fields(settings_dict):
    """Pure logic: given one connection's settings dict, returns
    (auto_connect, hidden, last_connected_iso). Two confirmed gotchas:
    `connection.autoconnect` defaults to `True` when absent (absent is
    not the same as explicit `False`); `802-11-wireless.hidden`
    defaults to `False`. `connection.timestamp` is converted via
    `_epoch_to_iso()` to the same ISO8601 shape iwd.py's
    `LastConnectedTime` produces, so nothing downstream needs to change
    for this backend.
    """
    connection = settings_dict.get("connection", {})
    wireless = settings_dict.get("802-11-wireless", {})

    autoconnect_prop = connection.get("autoconnect")
    auto_connect = autoconnect_prop[1] if autoconnect_prop is not None else True

    hidden_prop = wireless.get("hidden")
    hidden = hidden_prop[1] if hidden_prop is not None else False

    timestamp_prop = connection.get("timestamp")
    timestamp = timestamp_prop[1] if timestamp_prop is not None else 0
    last_connected = _epoch_to_iso(timestamp) if timestamp else None

    return auto_connect, hidden, last_connected


def _epoch_to_iso(epoch_seconds):
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_wifi_network(merged_ap, known_profile_settings):
    """Pure logic: assembles one WifiNetwork from a merged AP entry
    (see merge_access_points_by_ssid()) and its matching known
    profile's settings dict (or None if unknown) — same assembly role
    as iwd.py's own _build_wifi_network(). Testable without any D-Bus
    connection.
    """
    if known_profile_settings is not None:
        auto_connect, hidden, last_connected = known_profile_fields(known_profile_settings)
        known = True
    else:
        auto_connect = hidden = last_connected = None
        known = False
    return WifiNetwork(
        ssid=merged_ap["ssid"],
        connected=merged_ap["connected"],
        signal=merged_ap["strength"],
        known=known,
        security=merged_ap["security"],
        auto_connect=auto_connect,
        hidden=hidden,
        last_connected=last_connected,
    )


def _scan_completed(baseline, live):
    """Pure logic: NetworkManager exposes no boolean "scanning in
    progress" the way iwd's Station.Scanning is — Device.Wireless.
    LastScan is only an epoch-ms timestamp of the last COMPLETED scan.
    True once `live` has moved past what `LastScan` was right before
    RequestScan() was called (`baseline`). Testable without any D-Bus
    connection or real sleeps.
    """
    if baseline is None:
        return True
    if live is None:
        return False
    return live > baseline


def _scan_timed_out(started, now, timeout=SCAN_TIMEOUT_SECONDS):
    if started is None:
        return False
    return (now - started) > timeout


def _activation_outcome(state):
    """Pure logic: NMActiveConnectionState -> "pending"/"success"/
    "failure". Testable without any D-Bus connection."""
    if state == NM_ACTIVE_CONNECTION_STATE_ACTIVATED:
        return "success"
    if state == NM_ACTIVE_CONNECTION_STATE_DEACTIVATED:
        return "failure"
    return "pending"


def _failure_reason_message(reason_code):
    """Pure logic: NMDeviceStateReason -> a human-readable message,
    table-driven over the documented wifi-auth-relevant codes, with an
    honest fallback (the raw code) for anything not in that set —
    never silently claims a specific cause it doesn't actually know.
    """
    return _FAILURE_REASON_MESSAGES.get(reason_code, f"connection failed (reason {reason_code})")


class NetworkManagerBackend(WifiBackend):
    def __init__(self):
        # See is_scanning()'s own docstring — genuinely needs instance
        # state, unlike IwdBackend (stateless) or BluezBackend's own
        # _discovery_stop (which gates re-entrancy against a live,
        # trustworthy daemon boolean — no such boolean exists here).
        self._scan_baseline = None
        self._scan_started = None

    def _find_wifi_device(self, connection):
        device_paths = _call(connection, ROOT_PATH, IFACE_ROOT, "GetAllDevices")[0]
        device_types = []
        for path in device_paths:
            device_type = _call(
                connection, path, IFACE_PROPERTIES, "Get", "ss", (IFACE_DEVICE, "DeviceType"),
            )[0][1]
            device_types.append((path, device_type))
        return find_wifi_device_path(device_types)

    def _list_access_point_entries(self, connection, device_path):
        ap_paths = _call(connection, device_path, IFACE_WIRELESS, "GetAllAccessPoints")[0]
        active_ap_path = _call(
            connection, device_path, IFACE_PROPERTIES, "Get", "ss", (IFACE_WIRELESS, "ActiveAccessPoint"),
        )[0][1]
        entries = []
        for ap_path in ap_paths:
            props = _call(connection, ap_path, IFACE_PROPERTIES, "GetAll", "s", (IFACE_AP,))[0]
            ssid = decode_ssid(props["Ssid"][1])
            entries.append({
                "ssid": ssid,
                "strength": props["Strength"][1],
                "security": classify_security(props["Flags"][1], props["WpaFlags"][1], props["RsnFlags"][1]),
                "path": ap_path,
                "connected": ap_path == active_ap_path,
            })
        return entries

    def _list_connections_with_settings(self, connection):
        connection_paths = _call(connection, SETTINGS_PATH, IFACE_SETTINGS, "ListConnections")[0]
        result = []
        for path in connection_paths:
            settings = _call(connection, path, IFACE_CONNECTION, "GetSettings")[0]
            result.append((path, settings))
        return result

    def get_networks(self) -> list[WifiNetwork]:
        """Same no-internal-try/except discipline as IwdBackend.
        get_networks() (VISION.md's R3) — a D-Bus failure propagates
        naturally so StatusWorker's poll wrapper can record it as a
        real last_error instead of it vanishing into a bare `[]`.
        """
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            device_path = self._find_wifi_device(connection)
            if device_path is None:
                return []

            entries = self._list_access_point_entries(connection, device_path)
            merged = merge_access_points_by_ssid(entries)

            connections = self._list_connections_with_settings(connection)
            settings_by_path = dict(connections)

            networks = []
            for ap in merged:
                profile_path = find_known_profile_for_ssid(connections, ap["ssid"])
                profile_settings = settings_by_path.get(profile_path) if profile_path else None
                networks.append(_build_wifi_network(ap, profile_settings))
            return networks
        finally:
            connection.close()

    def connect(self, ssid: str) -> None:
        """Raises on failure — see this module's own docstring (point
        4) for why NetworkManager needs an actual bounded poll loop
        after activating, not iwd.py's one-shot post-call check.
        """
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            device_path = self._find_wifi_device(connection)
            if device_path is None:
                return

            entries = self._list_access_point_entries(connection, device_path)
            target = next((e for e in entries if e["ssid"] == ssid), None)
            if target is None:
                return

            connections = self._list_connections_with_settings(connection)
            profile_path = find_known_profile_for_ssid(connections, ssid)

            if profile_path is not None:
                active_path = _call(
                    connection, ROOT_PATH, IFACE_ROOT, "ActivateConnection",
                    "ooo", (profile_path, device_path, target["path"]),
                )[0]
            else:
                # A brand-new profile — just enough settings to identify
                # the network; NetworkManager itself asks our registered
                # NetworkManagerAgent for the passphrase via GetSecrets,
                # same one-keypress "Connect() just works, an agent
                # handles the interactive part" shape iwd.py already has.
                new_settings = {"802-11-wireless": {"ssid": ("ay", ssid.encode("utf-8"))}}
                reply = _call(
                    connection, ROOT_PATH, IFACE_ROOT, "AddAndActivateConnection",
                    "a{sa{sv}}oo", (new_settings, device_path, target["path"]),
                )
                active_path = reply[1]

            self._wait_for_activation(connection, active_path, device_path, ssid)
        finally:
            connection.close()

    def _wait_for_activation(self, connection, active_connection_path, device_path, ssid):
        deadline = time.monotonic() + CONNECT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            props = _call(
                connection, active_connection_path, IFACE_PROPERTIES, "GetAll", "s", (IFACE_ACTIVE_CONNECTION,),
            )[0]
            outcome = _activation_outcome(props["State"][1])
            if outcome == "success":
                return
            if outcome == "failure":
                _state, reason = _call(
                    connection, device_path, IFACE_PROPERTIES, "Get", "ss", (IFACE_DEVICE, "StateReason"),
                )[0][1]
                raise RuntimeError(f"Failed to connect to {ssid} — {_failure_reason_message(reason)}")
            time.sleep(CONNECT_POLL_INTERVAL_SECONDS)
        raise TimeoutError(f"Connecting to {ssid} timed out after {CONNECT_TIMEOUT_SECONDS}s")

    def disconnect(self) -> None:
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            device_path = self._find_wifi_device(connection)
            if device_path:
                _call(connection, device_path, IFACE_DEVICE, "Disconnect")
        finally:
            connection.close()

    def scan(self) -> None:
        """Fire-and-forget: RequestScan() schedules NetworkManager's
        own scan and returns immediately, same shape as iwd's Station.
        Scan(). See is_scanning() for how this backend tracks real
        completion (LastScan has no boolean equivalent to poll).
        """
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            device_path = self._find_wifi_device(connection)
            if device_path is None:
                return
            self._scan_baseline = self._get_last_scan(connection, device_path)
            self._scan_started = time.monotonic()
            _call(connection, device_path, IFACE_WIRELESS, "RequestScan", "a{sv}", ({},))
        finally:
            connection.close()

    def is_scanning(self) -> bool:
        """Real ground truth for whether a scan THIS backend instance
        itself triggered is still in progress — genuinely narrower
        than IwdBackend.is_scanning()/BluezBackend.is_discovering(),
        which both poll a live daemon boolean and so correctly report
        externally-triggered scans too. NetworkManager gives no way to
        detect an externally-triggered scan's progress at all (no
        boolean equivalent to iwd's Scanning exists) — this can only
        ever know about a scan started via this backend's own scan()
        call. Documented limitation, not silently accepted.
        """
        if self._scan_baseline is None:
            return False
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            device_path = self._find_wifi_device(connection)
            if device_path is None:
                self._scan_baseline = None
                self._scan_started = None
                return False
            live = self._get_last_scan(connection, device_path)
            if _scan_completed(self._scan_baseline, live) or _scan_timed_out(self._scan_started, time.monotonic()):
                self._scan_baseline = None
                self._scan_started = None
                return False
            return True
        finally:
            connection.close()

    def _get_last_scan(self, connection, device_path):
        return _call(connection, device_path, IFACE_PROPERTIES, "Get", "ss", (IFACE_WIRELESS, "LastScan"))[0][1]
