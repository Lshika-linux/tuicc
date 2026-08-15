"""Tests for modules/connectivity.py's _build_rows — the None-vs-[]
distinction VISION.md's R3 adds (a domain's last poll erroring vs. it
genuinely having nothing to report), and VISION.md's R4 follow-up that
put WiFi/Bluetooth behind the shared windowed_list.py fixed-slot-plus-
scroll mechanic (see _build_rows' own docstring for why — the box had
no real scrolling before this, WiFi hard-capped with a static
"+N more" line, Bluetooth not capped at all). draw()/nav_items()
themselves need a real curses screen, left untested here same as every
other module.
"""

from types import SimpleNamespace

from tuicc.connectivity.model import WifiNetwork, BluetoothDevice
from tuicc.modules.connectivity import (
    _action_progress_line,
    _build_rows,
    _bt_discover_preview_text,
    _bt_preview_text,
    _format_timestamp,
    _security_label,
    _selected_bt_index,
    _selected_wifi_index,
    _signal_bars,
    _wifi_preview_text,
    _wifi_scan_preview_text,
    next_browsing_selection,
    toggle_wifi,
    toggle_bluetooth,
)


def _ctx(wifi_networks=None, bluetooth_devices=None, wifi_error=None, bluetooth_error=None,
          selected_id=None, visible_slots=3):
    return SimpleNamespace(
        wifi_networks=wifi_networks,
        bluetooth_devices=bluetooth_devices,
        wifi_error=wifi_error,
        bluetooth_error=bluetooth_error,
        selected_id=selected_id,
        config=SimpleNamespace(connectivity_visible_slots=visible_slots),
    )


def _kinds(rows):
    return [kind for kind, _payload in rows]


# ---------- wifi: error vs empty vs cold-start ----------

def test_wifi_error_row_when_networks_none_and_error_set():
    ctx = _ctx(wifi_networks=None, wifi_error="D-Bus unreachable")

    rows = _build_rows(ctx, box_h=20)

    assert ("error", "D-Bus unreachable") in rows


def test_wifi_cold_start_none_with_no_error_renders_as_empty_slots_not_error():
    # Right after startup, before the first poll completes — nothing
    # has actually failed yet, must not look like an error. Renders as
    # `visible_slots` distinct "[empty - network N]" placeholders, not
    # one summary line (windowed_list.section_rows' own contract).
    ctx = _ctx(wifi_networks=None, wifi_error=None, visible_slots=3)

    rows = _build_rows(ctx, box_h=20)

    assert ("empty_slot", "[empty - network 1]") in rows
    assert ("empty_slot", "[empty - network 2]") in rows
    assert ("empty_slot", "[empty - network 3]") in rows
    assert "error" not in _kinds(rows)


def test_wifi_genuinely_empty_list_renders_same_as_cold_start():
    ctx = _ctx(wifi_networks=[], wifi_error=None, visible_slots=3)

    rows = _build_rows(ctx, box_h=20)

    assert ("empty_slot", "[empty - network 1]") in rows
    assert "error" not in _kinds(rows)


# ---------- bluetooth: error vs empty vs cold-start ----------

def test_bluetooth_error_row_when_devices_none_and_error_set():
    ctx = _ctx(bluetooth_devices=None, bluetooth_error="bluetoothctl timeout")

    rows = _build_rows(ctx, box_h=20)

    assert ("error", "bluetoothctl timeout") in rows


def test_bluetooth_cold_start_none_with_no_error_renders_as_empty_slots_not_error():
    ctx = _ctx(bluetooth_devices=None, bluetooth_error=None, visible_slots=3)

    rows = _build_rows(ctx, box_h=20)

    assert ("empty_slot", "[empty - device 1]") in rows
    assert "error" not in _kinds(rows)


def test_bluetooth_genuinely_empty_list_renders_same_as_cold_start():
    ctx = _ctx(bluetooth_devices=[], bluetooth_error=None, visible_slots=3)

    rows = _build_rows(ctx, box_h=20)

    assert ("empty_slot", "[empty - device 1]") in rows
    assert "error" not in _kinds(rows)


# ---------- both domains erroring independently ----------

def test_wifi_and_bluetooth_errors_are_independent():
    ctx = _ctx(
        wifi_networks=None, wifi_error="D-Bus unreachable",
        bluetooth_devices=[], bluetooth_error=None,
    )

    rows = _build_rows(ctx, box_h=20)

    assert ("error", "D-Bus unreachable") in rows
    assert ("empty_slot", "[empty - device 1]") in rows


# ---------- header includes the real item count ----------

def test_header_bare_when_count_unknown():
    ctx = _ctx(wifi_networks=None, bluetooth_devices=None)

    rows = _build_rows(ctx, box_h=20)

    assert ("wifi_header", "WiFi") in rows
    assert ("bt_header", "Bluetooth") in rows


def test_header_shows_zero_for_genuinely_empty_list():
    ctx = _ctx(wifi_networks=[], bluetooth_devices=[])

    rows = _build_rows(ctx, box_h=20)

    assert ("wifi_header", "WiFi [0]") in rows
    assert ("bt_header", "Bluetooth [0]") in rows


def test_header_shows_real_count_even_when_more_than_visible_slots():
    networks = [WifiNetwork(ssid=f"AP{i}", connected=False) for i in range(5)]
    ctx = _ctx(wifi_networks=networks, visible_slots=3)

    rows = _build_rows(ctx, box_h=20)

    assert ("wifi_header", "WiFi [5]") in rows


# ---------- R4: scan/discover triggers ----------
# Folded onto the SAME row as their section's own header (draw() paints
# header text left, trigger right) rather than a separate row below it
# — see _build_rows' own docstring for why. Still always present and
# still its own distinct NavItem/target_kind, just sharing a row with
# non-interactive header text.

def test_wifi_header_row_always_present():
    for ctx in (_ctx(), _ctx(wifi_networks=[]), _ctx(wifi_error="down")):
        assert any(kind == "wifi_header" for kind, _ in _build_rows(ctx, box_h=20))


def test_bt_header_row_always_present():
    for ctx in (_ctx(), _ctx(bluetooth_devices=[]), _ctx(bluetooth_error="down")):
        assert any(kind == "bt_header" for kind, _ in _build_rows(ctx, box_h=20))


def test_wifi_header_is_the_first_row():
    rows = _build_rows(_ctx(), box_h=20)

    assert rows[0] == ("wifi_header", "WiFi")


def test_bt_header_comes_right_after_the_spacer():
    rows = _build_rows(_ctx(), box_h=20)
    spacer_index = rows.index(("spacer", None))

    assert rows[spacer_index + 1] == ("bt_header", "Bluetooth")


# ---------- windowing: more items than visible_slots actually scrolls ----------

def test_wifi_section_shows_exactly_visible_slots_rows_when_over_capacity():
    networks = [WifiNetwork(ssid=f"AP{i}", connected=False) for i in range(10)]
    ctx = _ctx(wifi_networks=networks, visible_slots=3)

    rows = _build_rows(ctx, box_h=20)
    wifi_item_rows = [payload for kind, payload in rows if kind == "wifi_item"]

    assert len(wifi_item_rows) == 3
    assert [n.ssid for n in wifi_item_rows] == ["AP0", "AP1", "AP2"]


def test_wifi_section_window_follows_selection():
    networks = [WifiNetwork(ssid=f"AP{i}", connected=False) for i in range(10)]
    ctx = _ctx(wifi_networks=networks, visible_slots=3, selected_id="connectivity:wifi:AP7")

    rows = _build_rows(ctx, box_h=20)
    wifi_item_rows = [payload for kind, payload in rows if kind == "wifi_item"]

    # Selecting AP7 (index 7) must bring it into view — the window
    # shifts so it's the LAST visible slot (windowed_list.window_start's
    # own documented behavior), not scrolled further than necessary.
    assert [n.ssid for n in wifi_item_rows] == ["AP5", "AP6", "AP7"]


def test_bluetooth_section_not_pushed_off_by_a_long_wifi_scan():
    # The exact bug this refactor fixes: a long wifi network list used
    # to be able to consume unbounded box height and leave no room for
    # Bluetooth at all — now both sections are always exactly
    # visible_slots rows regardless of how many real items exist.
    networks = [WifiNetwork(ssid=f"AP{i}", connected=False) for i in range(30)]
    devices = [BluetoothDevice(id=f"AA:{i}", name=f"Dev{i}", connected=False) for i in range(5)]
    ctx = _ctx(wifi_networks=networks, bluetooth_devices=devices, visible_slots=3)

    rows = _build_rows(ctx, box_h=20)

    assert len([p for k, p in rows if k == "wifi_item"]) == 3
    assert len([p for k, p in rows if k == "bt_item"]) == 3
    assert ("bt_header", "Bluetooth [5]") in rows


# ---------- _selected_wifi_index / _selected_bt_index ----------

def test_selected_wifi_index_finds_matching_ssid():
    networks = [WifiNetwork(ssid="Home", connected=False), WifiNetwork(ssid="Office", connected=False)]

    assert _selected_wifi_index(networks, "connectivity:wifi:Office") == 1


def test_selected_wifi_index_none_when_not_a_wifi_selection():
    networks = [WifiNetwork(ssid="Home", connected=False)]

    assert _selected_wifi_index(networks, None) is None
    assert _selected_wifi_index(networks, "connectivity:bt:AA:BB") is None
    assert _selected_wifi_index(networks, "connectivity:wifi:header") is None


def test_selected_bt_index_finds_matching_id():
    devices = [BluetoothDevice(id="AA", name="A", connected=False), BluetoothDevice(id="BB", name="B", connected=False)]

    assert _selected_bt_index(devices, "connectivity:bt:BB") == 1


def test_selected_bt_index_none_when_not_a_bluetooth_selection():
    devices = [BluetoothDevice(id="AA", name="A", connected=False)]

    assert _selected_bt_index(devices, None) is None
    assert _selected_bt_index(devices, "connectivity:wifi:Home") is None
    assert _selected_bt_index(devices, "connectivity:bt:header") is None


# ---------- next_browsing_selection ----------
# Level-2 browsing's own Up/Down math — see connectivity.py's "level-2
# browsing" section docstring for why this exists instead of a
# separate index: loop_state.selected_id stays the single source of
# truth, this just derives the next one from it.

def test_next_browsing_selection_moves_forward_within_wifi():
    networks = [WifiNetwork(ssid="Home", connected=False), WifiNetwork(ssid="Office", connected=False)]

    result = next_browsing_selection("wifi", networks, "connectivity:wifi:Home", direction=1)

    assert result == "connectivity:wifi:Office"


def test_next_browsing_selection_moves_backward_within_bluetooth():
    devices = [BluetoothDevice(id="AA", name="A", connected=False), BluetoothDevice(id="BB", name="B", connected=False)]

    result = next_browsing_selection("bluetooth", devices, "connectivity:bt:BB", direction=-1)

    # id prefix is "bt", not "bluetooth" — must match _bt_row_nav_item's
    # own id convention, not the StatusWorker domain name.
    assert result == "connectivity:bt:AA"


def test_next_browsing_selection_wraps_past_the_last_item():
    networks = [WifiNetwork(ssid="Home", connected=False), WifiNetwork(ssid="Office", connected=False)]

    result = next_browsing_selection("wifi", networks, "connectivity:wifi:Office", direction=1)

    assert result == "connectivity:wifi:Home"


def test_next_browsing_selection_wraps_before_the_first_item():
    networks = [WifiNetwork(ssid="Home", connected=False), WifiNetwork(ssid="Office", connected=False)]

    result = next_browsing_selection("wifi", networks, "connectivity:wifi:Home", direction=-1)

    assert result == "connectivity:wifi:Office"


def test_next_browsing_selection_defaults_to_first_item_when_nothing_matches():
    networks = [WifiNetwork(ssid="Home", connected=False), WifiNetwork(ssid="Office", connected=False)]

    assert next_browsing_selection("wifi", networks, None, direction=1) == "connectivity:wifi:Home"
    assert next_browsing_selection("wifi", networks, "connectivity:wifi:Gone", direction=1) == "connectivity:wifi:Home"


# ---------- toggle_wifi / toggle_bluetooth ----------

def test_toggle_wifi_connects_when_not_connected():
    status = _FakeStatus(domains={"wifi": [WifiNetwork(ssid="Home", connected=False)]})

    toggle_wifi(status, "Home")

    assert status.requested_actions == [("wifi", "connect", "Home")]


def test_toggle_wifi_disconnects_when_already_connected():
    status = _FakeStatus(domains={"wifi": [WifiNetwork(ssid="Home", connected=True)]})

    toggle_wifi(status, "Home")

    assert status.requested_actions == [("wifi", "disconnect", "Home")]


def test_toggle_bluetooth_connects_when_not_connected():
    status = _FakeStatus(domains={"bluetooth": [BluetoothDevice(id="AA", name="A", connected=False)]})

    toggle_bluetooth(status, "AA")

    assert status.requested_actions == [("bluetooth", "connect", "AA")]


def test_toggle_bluetooth_disconnects_when_already_connected():
    status = _FakeStatus(domains={"bluetooth": [BluetoothDevice(id="AA", name="A", connected=True)]})

    toggle_bluetooth(status, "AA")

    assert status.requested_actions == [("bluetooth", "disconnect", "AA")]


# ---------- _signal_bars ----------
# See CLAUDE/NOTES/design-decisions.md#connectivity-module-design for
# why this is always all 5 segments (filled or empty), plain Unicode
# (▮/▯), not a Nerd Font icon.

def test_signal_bars_always_five_segments():
    for signal in (None, 0, 1, 20, 21, 50, 75, 99, 100):
        assert len(_signal_bars(signal)) == 5


def test_signal_bars_none_is_all_empty():
    assert _signal_bars(None) == "▯▯▯▯▯"


def test_signal_bars_zero_is_all_empty():
    assert _signal_bars(0) == "▯▯▯▯▯"


def test_signal_bars_full_signal_is_all_filled():
    assert _signal_bars(100) == "▮▮▮▮▮"


def test_signal_bars_any_nonzero_signal_shows_at_least_one_filled():
    # However weak — must never look identical to "no signal at all".
    assert _signal_bars(1) == "▮▯▯▯▯"


def test_signal_bars_bucket_boundaries():
    assert _signal_bars(20) == "▮▯▯▯▯"   # top of bucket 1
    assert _signal_bars(21) == "▮▮▯▯▯"   # bottom of bucket 2
    assert _signal_bars(40) == "▮▮▯▯▯"
    assert _signal_bars(41) == "▮▮▮▯▯"
    assert _signal_bars(60) == "▮▮▮▯▯"
    assert _signal_bars(61) == "▮▮▮▮▯"
    assert _signal_bars(80) == "▮▮▮▮▯"
    assert _signal_bars(81) == "▮▮▮▮▮"


# ---------- _security_label / _format_timestamp ----------

def test_security_label_known_values():
    assert _security_label("open") == "Open (no security)"
    assert _security_label("psk") == "WPA/WPA2-Personal"
    assert _security_label("8021x") == "Enterprise (802.1x)"


def test_security_label_networkmanager_only_values():
    # Reachable only via the NetworkManager backend's own
    # classify_security() — iwd's Network.Type has no equivalent.
    assert _security_label("wep") == "WEP"
    assert _security_label("sae") == "WPA3-Personal (SAE)"
    assert _security_label("owe") == "Enhanced Open (OWE)"


def test_security_label_unknown_value_falls_back_to_raw_string():
    # A future backend security value this mapping doesn't know about
    # yet must still show SOMETHING real, not crash or silently say
    # "unknown". "sae" used to be this test's example until
    # networkmanager.py's classify_security() made it a real,
    # recognized token (see _WIFI_SECURITY_LABELS) — swapped for a
    # token that stays genuinely unrecognized.
    assert _security_label("wpa3-enterprise-suite-b") == "wpa3-enterprise-suite-b"


def test_security_label_none_is_unknown():
    assert _security_label(None) == "unknown"


def test_format_timestamp_parses_iso8601_utc():
    # Exact wall-clock result depends on the test machine's local
    # timezone, so just check it doesn't fall back to the raw string
    # and produces the expected shape.
    result = _format_timestamp("2026-08-10T05:35:00Z")

    assert result != "2026-08-10T05:35:00Z"
    assert len(result) == len("YYYY-MM-DD HH:MM")


def test_format_timestamp_falls_back_to_raw_string_on_unparseable_input():
    assert _format_timestamp("not a timestamp") == "not a timestamp"


# ---------- _wifi_preview_text / _bt_preview_text ----------

def _theme():
    return {"accent": 1, "text": 2, "urgent": 3}


def _cfg():
    return SimpleNamespace(keybinds={"scan": ord("s")})


def _text_of(lines):
    return [text for text, _color in lines]


def test_wifi_preview_text_includes_core_fields():
    network = WifiNetwork(ssid="Home", connected=True, signal=80, known=True, security="psk")

    lines = _text_of(_wifi_preview_text(network, _theme(), None, _cfg()))

    assert "Home" in lines
    assert "Signal: 80%" in lines
    assert "Security: WPA/WPA2-Personal" in lines
    assert "Known: yes" in lines
    assert "Connected: yes" in lines


def test_wifi_preview_text_omits_known_network_fields_when_unknown():
    network = WifiNetwork(ssid="Stranger", connected=False, signal=40, known=False, security="open")

    lines = _text_of(_wifi_preview_text(network, _theme(), None, _cfg()))

    assert not any(line.startswith("Auto-connect:") for line in lines)
    assert not any(line.startswith("Hidden:") for line in lines)
    assert not any(line.startswith("Last connected:") for line in lines)


def test_wifi_preview_text_includes_known_network_fields_when_known():
    network = WifiNetwork(
        ssid="Home", connected=True, signal=90, known=True, security="psk",
        auto_connect=True, hidden=False, last_connected="2026-08-10T05:35:00Z",
    )

    lines = _text_of(_wifi_preview_text(network, _theme(), None, _cfg()))

    assert "Auto-connect: yes" in lines
    assert "Hidden: no" in lines
    assert any(line.startswith("Last connected: ") for line in lines)


def test_wifi_preview_text_signal_unknown_when_none():
    network = WifiNetwork(ssid="Home", connected=False, signal=None, known=True)

    lines = _text_of(_wifi_preview_text(network, _theme(), None, _cfg()))

    assert "Signal: unknown" in lines


def test_bt_preview_text_includes_core_fields():
    device = BluetoothDevice(
        id="AA:BB:CC:DD:EE:FF", name="Speaker", connected=True, battery=55, paired=True,
        trusted=True, blocked=False, icon="audio-headphones", address_type="public",
    )

    lines = _text_of(_bt_preview_text(device, _theme(), None, _cfg()))

    assert "Speaker" in lines
    assert "Address: AA:BB:CC:DD:EE:FF" in lines
    assert "Address type: public" in lines
    assert "Icon: audio-headphones" in lines
    assert "Paired: yes" in lines
    assert "Trusted: yes" in lines
    assert "Blocked: no" in lines
    assert "Connected: yes" in lines
    assert "Battery: 55%" in lines


def test_bt_preview_text_omits_optional_fields_when_absent():
    device = BluetoothDevice(id="AA", name="Unknown Device", connected=False, paired=False)

    lines = _text_of(_bt_preview_text(device, _theme(), None, _cfg()))

    assert not any(line.startswith("Icon:") for line in lines)
    assert not any(line.startswith("Address type:") for line in lines)
    assert not any(line.startswith("Battery:") for line in lines)
    assert not any(line.startswith("RSSI:") for line in lines)


def test_bt_preview_text_blocked_uses_urgent_color():
    device = BluetoothDevice(id="AA", name="Blocked One", connected=False, blocked=True)
    theme = _theme()

    lines = _bt_preview_text(device, theme, None, _cfg())
    blocked_line = next(line for line in lines if line[0] == "Blocked: yes")

    assert blocked_line[1] == theme["urgent"]


# ---------- _action_progress_line: connect/disconnect feedback ----------
# See CLAUDE/NOTES/design-decisions.md#connectivity-module-design —
# pressing Enter alone gave no feedback beyond the row's own blink, and
# no way to tell why a connect failed.

class _FakeStatus:
    def __init__(self, pending_keys=(), errors_for=None, domains=None):
        self._pending_keys = set(pending_keys)
        self._errors_for = errors_for or {}
        self._domains = domains or {}
        self.requested_actions = []  # [(domain_name, action, arg), ...] — toggle_wifi/toggle_bluetooth tests

    def is_pending(self, domain_name, key):
        return (domain_name, key) in self._pending_keys

    def get_action_error_for(self, domain_name, key):
        return self._errors_for.get((domain_name, key))

    def get(self, domain_name):
        return self._domains.get(domain_name)

    def request_action(self, domain_name, action, arg):
        self.requested_actions.append((domain_name, action, arg))


def test_action_progress_line_none_when_nothing_happening():
    status = _FakeStatus()

    assert _action_progress_line(status, "wifi", "Home", connected=False, theme=_theme()) is None


def test_action_progress_line_none_when_status_is_none():
    assert _action_progress_line(None, "wifi", "Home", connected=False, theme=_theme()) is None


def test_action_progress_line_connecting_when_pending_and_not_yet_connected():
    status = _FakeStatus(pending_keys={("wifi", "Home")})

    text, _color = _action_progress_line(status, "wifi", "Home", connected=False, theme=_theme())

    assert text == "Connecting…"


def test_action_progress_line_disconnecting_when_pending_and_already_connected():
    status = _FakeStatus(pending_keys={("wifi", "Home")})

    text, _color = _action_progress_line(status, "wifi", "Home", connected=True, theme=_theme())

    assert text == "Disconnecting…"


def test_action_progress_line_shows_the_real_error_once_settled():
    status = _FakeStatus(errors_for={("wifi", "Home"): "Operation failed"})

    text, color = _action_progress_line(status, "wifi", "Home", connected=False, theme=_theme())

    assert text == "⚠ Operation failed"
    assert color == _theme()["urgent"]


def test_action_progress_line_pending_takes_priority_over_a_stale_error():
    # get_action_error_for() only clears once the NEXT action for this
    # key finishes — while a fresh attempt is in flight, is_pending()
    # must win, not a leftover error from the attempt before it.
    status = _FakeStatus(pending_keys={("wifi", "Home")}, errors_for={("wifi", "Home"): "stale error"})

    text, _color = _action_progress_line(status, "wifi", "Home", connected=False, theme=_theme())

    assert text == "Connecting…"


def test_wifi_preview_text_includes_progress_line_when_pending():
    network = WifiNetwork(ssid="Home", connected=False, signal=50, known=True, security="psk")
    status = _FakeStatus(pending_keys={("wifi", "Home")})

    lines = _text_of(_wifi_preview_text(network, _theme(), status, _cfg()))

    assert "Connecting…" in lines


def test_bt_preview_text_includes_error_when_settled_and_failed():
    device = BluetoothDevice(id="AA:BB", name="Speaker", connected=False)
    status = _FakeStatus(errors_for={("bluetooth", "AA:BB"): "org.bluez.Error.Failed"})

    lines = _text_of(_bt_preview_text(device, _theme(), status, _cfg()))

    assert "⚠ org.bluez.Error.Failed" in lines


def test_preview_text_progress_scoped_to_the_right_key_only():
    # The exact bug get_action_error_for() exists to prevent — a
    # DIFFERENT network's stale error must never bleed onto this one.
    network = WifiNetwork(ssid="Home", connected=False, signal=50, known=True, security="psk")
    status = _FakeStatus(errors_for={("wifi", "Office"): "some other network's error"})

    lines = _text_of(_wifi_preview_text(network, _theme(), status, _cfg()))

    assert not any(line.startswith("⚠") for line in lines)


# ---------- _wifi_scan_preview_text / _bt_discover_preview_text ----------
# Hovering the Scan/Discover row itself used to show the default
# (unrelated) preview.py content; this is the full list at a glance,
# not just the connectivity_visible_slots window the box's own
# scrollable section shows.

def test_wifi_scan_preview_lists_every_network_not_just_the_window():
    networks = [WifiNetwork(ssid=f"AP{i}", connected=False, signal=50) for i in range(10)]

    lines = _text_of(_wifi_scan_preview_text(networks, None, _theme()))

    assert "Available networks [10]" in lines
    assert any("AP0" in line for line in lines)
    assert any("AP9" in line for line in lines)


def test_wifi_scan_preview_marks_new_networks():
    networks = [WifiNetwork(ssid="Stranger", connected=False, signal=40, known=False)]

    lines = _text_of(_wifi_scan_preview_text(networks, None, _theme()))

    assert any("[new] Stranger" in line for line in lines)


def test_wifi_scan_preview_empty_list_says_so():
    lines = _text_of(_wifi_scan_preview_text([], None, _theme()))

    assert lines == ["No networks found"]


def test_wifi_scan_preview_shows_error_when_none_with_error():
    lines = _text_of(_wifi_scan_preview_text(None, "D-Bus unreachable", _theme()))

    assert lines == ["⚠ D-Bus unreachable"]


def test_wifi_scan_preview_cold_start_none_no_error_is_empty_not_error():
    lines = _text_of(_wifi_scan_preview_text(None, None, _theme()))

    assert lines == ["No networks found"]


def test_bt_discover_preview_lists_every_device():
    devices = [BluetoothDevice(id=f"AA:{i}", name=f"Dev{i}", connected=False) for i in range(4)]

    lines = _text_of(_bt_discover_preview_text(devices, None, _theme()))

    assert "Available devices [4]" in lines
    assert any("Dev0" in line for line in lines)
    assert any("Dev3" in line for line in lines)


def test_bt_discover_preview_marks_unpaired_devices():
    devices = [BluetoothDevice(id="AA", name="Stranger", connected=False, paired=False)]

    lines = _text_of(_bt_discover_preview_text(devices, None, _theme()))

    assert any("[new] Stranger" in line for line in lines)


def test_bt_discover_preview_shows_error_when_none_with_error():
    lines = _text_of(_bt_discover_preview_text(None, "bluez unreachable", _theme()))

    assert lines == ["⚠ bluez unreachable"]
