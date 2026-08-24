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

import curses
import re
from types import SimpleNamespace

from tuicc.connectivity.model import WifiNetwork, BluetoothDevice, AdapterInfo, ConnectionDiagnostics, BluetoothAdapterInfo
from tuicc.modules import connectivity as connectivity_module
from tuicc.modules.connectivity import (
    _action_progress_line,
    _power_progress_line,
    _adapter_info_table,
    _bt_header_status_segments,
    _connection_diagnostics_table,
    _forget_hidden_hint_segments,
    _is_header_action_flashing,
    _preview_available,
    flash_header_action,
    guarantee_scan_blink,
    is_scan_blink_guaranteed,
    _wifi_preview_tables,
    _bt_adapter_info_table,
    _bt_preview_tables,
    _scanning_dot,
    _status_dot,
    _browsing_hint_footer,
    _empty_browsing_nav_item,
    _build_rows,
    _connected_first,
    _bt_discover_preview_text,
    _bt_preview_text,
    _format_timestamp,
    _header_enter_hint_footer,
    _security_label,
    _selected_bt_index,
    _selected_wifi_index,
    _signal_bars,
    _wifi_header_status_segments,
    _wifi_preview_text,
    _wifi_property_rows,
    _wifi_row_nav_item,
    _wifi_scan_preview_text,
    next_browsing_selection,
    toggle_wifi,
    toggle_bluetooth,
    is_confirming_forget,
    confirming_forget_ssid,
    request_forget,
    cancel_forget,
    is_entering_hidden_ssid,
    start_hidden_ssid_entry,
    cancel_hidden_ssid_entry,
    handle_hidden_ssid_key,
    apply_hidden_ssid,
    required_fh,
)


# ---------- required_fh ----------

def test_required_fh_is_two_sections_plus_border():
    # header + visible_slots + spacer + header + visible_slots, + 2 border.
    cfg = SimpleNamespace(connectivity_visible_slots=3)

    assert required_fh(cfg) == 2 * 3 + 5


def test_required_fh_scales_with_visible_slots():
    cfg = SimpleNamespace(connectivity_visible_slots=5)

    assert required_fh(cfg) == 2 * 5 + 5


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


# ---------- _connected_first / connected devices sort to the top ----------
# Applied to BOTH wifi and bluetooth (see _connected_first's own
# docstring for the full "why" and its history) — bluez's
# GetManagedObjects() walk has no inherent ordering guarantee (found
# live, Rafi's own report: a connected device sitting third in a
# 3-slot windowed view), and neither does NetworkManager's own
# get_networks() (found live testing that backend for real — unlike
# iwd's GetOrderedNetworks, which already hands back the connected
# network first on its own, no client-side sort needed there, but
# harmless to also run this sort over an already-sorted iwd list).

def test_connected_first_sorts_connected_device_to_front():
    a = BluetoothDevice(id="AA", name="A", connected=False)
    b = BluetoothDevice(id="BB", name="B", connected=True)
    c = BluetoothDevice(id="CC", name="C", connected=False)

    assert _connected_first([a, b, c]) == [b, a, c]


def test_connected_first_is_stable_among_ties():
    a = BluetoothDevice(id="AA", name="A", connected=False)
    b = BluetoothDevice(id="BB", name="B", connected=False)

    assert _connected_first([a, b]) == [a, b]


def test_connected_first_none_stays_none():
    assert _connected_first(None) is None


def test_build_rows_bluetooth_connected_device_sorts_first():
    devices = [
        BluetoothDevice(id="AA", name="Keyboard", connected=False),
        BluetoothDevice(id="BB", name="MAJOR IV", connected=True),
        BluetoothDevice(id="CC", name="Mouse", connected=False),
    ]
    ctx = _ctx(bluetooth_devices=devices, visible_slots=3)

    rows = _build_rows(ctx, box_h=20)
    bt_items = [payload for kind, payload in rows if kind == "bt_item"]

    assert bt_items[0].name == "MAJOR IV"


def test_connected_first_sorts_connected_wifi_network_to_front():
    # The NetworkManager backend's own get_networks() (unlike iwd's
    # GetOrderedNetworks) has no inherent ordering — found live testing
    # it for real, same class of gap bluez's own device list already
    # had.
    a = WifiNetwork(ssid="Guest", connected=False)
    b = WifiNetwork(ssid="Home", connected=True)
    c = WifiNetwork(ssid="Neighbor", connected=False)

    assert _connected_first([a, b, c]) == [b, a, c]


def test_build_rows_wifi_connected_network_sorts_first():
    networks = [
        WifiNetwork(ssid="Guest", connected=False),
        WifiNetwork(ssid="Home", connected=True),
        WifiNetwork(ssid="Neighbor", connected=False),
    ]
    ctx = _ctx(wifi_networks=networks, visible_slots=3)

    rows = _build_rows(ctx, box_h=20)
    wifi_items = [payload for kind, payload in rows if kind == "wifi_item"]

    assert wifi_items[0].ssid == "Home"


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
    assert _selected_wifi_index(networks, "connectivity:wifi:scan") is None


def test_selected_bt_index_finds_matching_id():
    devices = [BluetoothDevice(id="AA", name="A", connected=False), BluetoothDevice(id="BB", name="B", connected=False)]

    assert _selected_bt_index(devices, "connectivity:bt:BB") == 1


def test_selected_bt_index_none_when_not_a_bluetooth_selection():
    devices = [BluetoothDevice(id="AA", name="A", connected=False)]

    assert _selected_bt_index(devices, None) is None
    assert _selected_bt_index(devices, "connectivity:wifi:Home") is None
    assert _selected_bt_index(devices, "connectivity:bt:header") is None
    assert _selected_bt_index(devices, "connectivity:bt:scan") is None


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
    return SimpleNamespace(keybinds={
        "scan": ord("s"), "confirm": ord("\n"),
        "wifi_forget": ord("f"), "wifi_connect_hidden": ord("h"), "wifi_power_toggle": ord("p"),
        "bt_power_toggle": ord("p"), "bt_pairable_toggle": ord("a"),
    })


def _text_of(lines):
    return [text for text, _color in lines]


def test_wifi_preview_text_includes_core_fields():
    network = WifiNetwork(ssid="Home", connected=True, signal=80, known=True, security="psk")

    texts = _text_of(_wifi_preview_text(network, _theme(), None))

    assert "Home" in texts
    assert any(text.startswith("Signal:") and text.endswith("80%") for text in texts)
    assert any(text.startswith("Security:") and text.endswith("WPA/WPA2-Personal") for text in texts)
    assert any(text.startswith("Known:") and text.endswith("yes") for text in texts)
    assert any(text.startswith("Connected:") and text.endswith("yes") for text in texts)


def test_wifi_preview_text_omits_known_network_fields_when_unknown():
    network = WifiNetwork(ssid="Stranger", connected=False, signal=40, known=False, security="open")

    texts = _text_of(_wifi_preview_text(network, _theme(), None))

    assert not any(text.startswith("Auto-connect:") for text in texts)
    assert not any(text.startswith("Hidden:") for text in texts)
    assert not any(text.startswith("Last connected:") for text in texts)


def test_wifi_preview_text_includes_known_network_fields_when_known():
    network = WifiNetwork(
        ssid="Home", connected=True, signal=90, known=True, security="psk",
        auto_connect=True, hidden=False, last_connected="2026-08-10T05:35:00Z",
    )

    texts = _text_of(_wifi_preview_text(network, _theme(), None))

    assert any(text.startswith("Auto-connect:") and text.endswith("yes") for text in texts)
    assert any(text.startswith("Hidden:") and text.endswith("no") for text in texts)
    assert any(text.startswith("Last connected:") for text in texts)


def test_wifi_preview_text_signal_unknown_when_none():
    network = WifiNetwork(ssid="Home", connected=False, signal=None, known=True)

    texts = _text_of(_wifi_preview_text(network, _theme(), None))

    assert any(text.startswith("Signal:") and text.endswith("unknown") for text in texts)


def test_wifi_preview_text_has_a_blank_line_between_every_row():
    # "give it breathing room" — Rafi's own call, 2026-08-15.
    network = WifiNetwork(ssid="Home", connected=True, signal=80, known=True, security="psk")

    texts = _text_of(_wifi_preview_text(network, _theme(), None))

    assert texts[0] == "Home"
    assert texts[1] == ""
    assert texts[2] != ""


def test_wifi_preview_text_labels_are_padded_to_a_shared_width():
    # The whole point: every value should start in the same column
    # regardless of its own label's length ("Signal:" vs "Connected:").
    network = WifiNetwork(ssid="Home", connected=True, signal=80, known=True, security="psk")

    texts = [text for text in _text_of(_wifi_preview_text(network, _theme(), None)) if text]
    value_columns = {m.start(1) for text in texts if (m := re.match(r"\S+:\s+(\S)", text))}

    assert len(value_columns) == 1


def test_bt_preview_text_includes_core_fields():
    device = BluetoothDevice(
        id="AA:BB:CC:DD:EE:FF", name="Speaker", connected=True, battery=55, paired=True,
        trusted=True, blocked=False, icon="audio-headphones", address_type="public",
    )

    lines = _text_of(_bt_preview_text(device, _theme(), None))

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

    lines = _text_of(_bt_preview_text(device, _theme(), None))

    assert not any(line.startswith("Icon:") for line in lines)
    assert not any(line.startswith("Address type:") for line in lines)
    assert not any(line.startswith("Battery:") for line in lines)
    assert not any(line.startswith("RSSI:") for line in lines)


def test_bt_preview_text_blocked_uses_urgent_color():
    device = BluetoothDevice(id="AA", name="Blocked One", connected=False, blocked=True)
    theme = _theme()

    lines = _bt_preview_text(device, theme, None)
    blocked_line = next(line for line in lines if line[0] == "Blocked: yes")

    assert blocked_line[1] == theme["urgent"]


# ---------- _wifi_row_nav_item: browsing a row keeps the network list ----------
# A side-by-side "Available networks | Selected" panel briefly lived
# here (2026-08-16/17, _wifi_networks_side_panel) — reverted the same
# session, Rafi's own call: it just duplicated the WiFi box's own row
# list (dot/[new]/signal%) a second time, with no new information. The
# row's own preview is back to plain per-network detail only.

def test_wifi_row_nav_item_shows_plain_network_detail():
    network = WifiNetwork(ssid="Home", connected=True, signal=80, known=True, security="psk")

    item = _wifi_row_nav_item(
        network, box=(0, 0, 40, 20), row=5, theme=_theme(), status=None, cfg=_cfg(),
        adapter_info=None, scanning=False, diagnostics=None,
    )

    assert _text_of(item.preview_text)[0] == "Home"


def test_wifi_property_rows_matches_wifi_preview_text_content():
    # The extracted shared helper (_wifi_property_rows) should produce
    # exactly the label:value pairs _wifi_preview_text's own padded
    # display already showed — same data, two different presentations.
    network = WifiNetwork(ssid="Home", connected=True, signal=80, known=True, security="psk")

    rows = _wifi_property_rows(network, _theme())

    labels = [label for label, _value, _color in rows]
    assert labels == ["Signal:", "Security:", "Known:", "Auto-connect:", "Hidden:", "Connected:"]


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

    lines = _wifi_preview_text(network, _theme(), status)

    assert "Connecting…" in _text_of(lines)


def test_bt_preview_text_includes_error_when_settled_and_failed():
    device = BluetoothDevice(id="AA:BB", name="Speaker", connected=False)
    status = _FakeStatus(errors_for={("bluetooth", "AA:BB"): "org.bluez.Error.Failed"})

    lines = _text_of(_bt_preview_text(device, _theme(), status))

    assert "⚠ org.bluez.Error.Failed" in lines


def test_wifi_preview_text_progress_scoped_to_the_right_key_only():
    # The exact bug get_action_error_for() exists to prevent — a
    # DIFFERENT network's stale error must never bleed onto this one.
    network = WifiNetwork(ssid="Home", connected=False, signal=50, known=True, security="psk")
    status = _FakeStatus(errors_for={("wifi", "Office"): "some other network's error"})

    lines = _wifi_preview_text(network, _theme(), status)

    assert not any(line.startswith("⚠") for line in _text_of(lines))


# ---------- _power_progress_line: live radio power-toggle feedback ----------
# Rafi's own follow-up ask, once the toggle itself was confirmed
# working live: nothing on screen said "Powering on…" while the
# request was actually in flight.

def test_power_progress_line_none_when_nothing_happening():
    status = _FakeStatus()

    assert _power_progress_line(status, AdapterInfo(powered=True), _theme()) is None


def test_power_progress_line_none_when_status_is_none():
    assert _power_progress_line(None, AdapterInfo(powered=True), _theme()) is None


def test_power_progress_line_powering_on_when_last_known_state_was_off():
    status = _FakeStatus(pending_keys={("wifi", "power")})

    text, _color = _power_progress_line(status, AdapterInfo(powered=False), _theme())

    assert text == "Powering on…"


def test_power_progress_line_powering_off_when_last_known_state_was_on():
    status = _FakeStatus(pending_keys={("wifi", "power")})

    text, _color = _power_progress_line(status, AdapterInfo(powered=True), _theme())

    assert text == "Powering off…"


def test_power_progress_line_powering_on_when_adapter_info_missing():
    # No last-known state to go on at all — defaults to the more
    # common "was off, turning on" guess rather than crashing.
    status = _FakeStatus(pending_keys={("wifi", "power")})

    text, _color = _power_progress_line(status, None, _theme())

    assert text == "Powering on…"


def test_power_progress_line_shows_the_real_error_once_settled():
    status = _FakeStatus(errors_for={("wifi", "power"): "Operation not permitted"})

    text, color = _power_progress_line(status, AdapterInfo(powered=True), _theme())

    assert text == "⚠ Operation not permitted"
    assert color == _theme()["urgent"]


def test_power_progress_line_only_checks_its_own_fixed_key():
    # Not scoped by ssid/device id like _action_progress_line — a
    # connect attempt pending for some unrelated key must not be
    # mistaken for a power toggle in flight.
    status = _FakeStatus(pending_keys={("wifi", "Home")})

    assert _power_progress_line(status, AdapterInfo(powered=True), _theme()) is None


# ---------- _wifi_scan_preview_text / _bt_discover_preview_text ----------
# Hovering the header row itself used to show the default (unrelated)
# preview.py content; this is the full list at a glance, not just the
# connectivity_visible_slots window the box's own scrollable section
# shows. The "[Enter] Browse ..." discoverability hint lives in a
# SEPARATE preview_footer (see NavItem.preview_footer's own docstring
# and _header_enter_hint_footer's own tests below), not appended into
# this list.

def test_wifi_scan_preview_lists_every_network_not_just_the_window():
    networks = [WifiNetwork(ssid=f"AP{i}", connected=False, signal=50) for i in range(10)]

    texts = _text_of(_wifi_scan_preview_text(networks, None, _theme()))

    assert "Available networks [10]" in texts
    assert any("AP0" in text for text in texts)
    assert any("AP9" in text for text in texts)


def test_wifi_scan_preview_marks_new_networks():
    networks = [WifiNetwork(ssid="Stranger", connected=False, signal=40, known=False)]

    texts = _text_of(_wifi_scan_preview_text(networks, None, _theme()))

    assert any("[new] Stranger" in text for text in texts)


def test_wifi_scan_preview_has_a_blank_line_between_every_row():
    networks = [WifiNetwork(ssid="Home", connected=False, signal=50)]

    texts = _text_of(_wifi_scan_preview_text(networks, None, _theme()))

    assert texts[0] == "Available networks [1]"
    assert texts[1] == ""
    assert texts[2] != ""


def test_wifi_scan_preview_signal_lands_in_a_shared_column():
    networks = [
        WifiNetwork(ssid="A", connected=False, signal=50),
        WifiNetwork(ssid="LongerNetworkName", connected=False, signal=90),
    ]

    texts = [text for text in _text_of(_wifi_scan_preview_text(networks, None, _theme())) if text and "[" not in text]

    assert len({text.rindex(" ") for text in texts}) == 1


def test_wifi_scan_preview_includes_power_progress_row():
    networks = [WifiNetwork(ssid="Home", connected=False, signal=50)]
    status = _FakeStatus(pending_keys={("wifi", "power")})

    texts = _text_of(_wifi_scan_preview_text(networks, None, _theme(), status, AdapterInfo(powered=True)))

    assert "Powering off…" in texts


def test_wifi_scan_preview_empty_list_says_so():
    lines = _text_of(_wifi_scan_preview_text([], None, _theme()))

    assert lines == ["No networks found"]


def test_wifi_scan_preview_shows_error_when_none_with_error():
    lines = _text_of(_wifi_scan_preview_text(None, "D-Bus unreachable", _theme()))

    assert lines == ["⚠ D-Bus unreachable"]


def test_wifi_scan_preview_cold_start_none_no_error_is_empty_not_error():
    lines = _text_of(_wifi_scan_preview_text(None, None, _theme()))

    assert lines == ["No networks found"]


def test_bt_discover_preview_idle_shows_the_full_list_not_just_paired():
    # Reverted the idle-only paired-filter (2026-08-17, Rafi's own
    # report) — this preview's own count must always match the
    # sidebar's honest full count, scan or not. Unpaired devices still
    # get "[new]" (a persisted bluez fact, not scan-dependent); paired,
    # not-connected devices get no status word (nothing honest to say
    # about range without an active scan).
    devices = [
        BluetoothDevice(id="AA", name="Speaker", connected=False, paired=True),
        BluetoothDevice(id="BB", name="Stranger", connected=False, paired=False),
    ]

    lines = _text_of(_bt_discover_preview_text(devices, None, _theme()))

    assert "Devices [2]" in lines
    assert any("Speaker" in line for line in lines)
    assert any("Stranger" in line and "[new]" in line for line in lines)
    assert not any("Speaker" in line and "[" in line for line in lines)


def test_bt_discover_preview_discovering_adds_range_status_for_known_devices():
    devices = [
        BluetoothDevice(id="AA", name="Speaker", connected=False, paired=True, rssi=-60),
        BluetoothDevice(id="BB", name="Stranger", connected=False, paired=False),
    ]

    lines = _text_of(_bt_discover_preview_text(devices, None, _theme(), discovering=True))

    assert "Devices [2]" in lines
    assert any("Speaker" in line and "[known; detected]" in line for line in lines)
    assert any("Stranger" in line and "[new]" in line for line in lines)


def test_bt_discover_preview_discovering_paired_not_recently_seen_is_undetected():
    devices = [BluetoothDevice(id="AA", name="Speaker", connected=False, paired=True, rssi=None)]

    lines = _text_of(_bt_discover_preview_text(devices, None, _theme(), discovering=True))

    assert any("[known; undetected]" in line for line in lines)


def test_bt_discover_preview_connected_device_gets_its_own_status_even_when_idle():
    # "Connected" is a real-time D-Bus fact, not scan-result-dependent
    # — unlike detected/undetected, it stays honest to show whether or
    # not a scan is currently running. A real, live-found case: MAJOR
    # IV, connected and playing audio, still rssi=None since a
    # classic-BT connection doesn't keep advertising the way an
    # idle-but-nearby device does. Checked before rssi, so it wins
    # regardless of discovering.
    devices = [BluetoothDevice(id="AA", name="MAJOR IV", connected=True, paired=True, rssi=None)]

    lines = _text_of(_bt_discover_preview_text(devices, None, _theme(), discovering=False))

    assert any("[known; connected]" in line for line in lines)
    assert not any("[known; detected]" in line for line in lines)


def test_bt_discover_preview_shows_error_when_none_with_error():
    lines = _text_of(_bt_discover_preview_text(None, "bluez unreachable", _theme()))

    assert lines == ["⚠ bluez unreachable"]


def test_bt_discover_preview_has_a_blank_line_between_every_row():
    devices = [BluetoothDevice(id="AA", name="Speaker", connected=False, paired=True)]

    lines = _text_of(_bt_discover_preview_text(devices, None, _theme()))

    assert lines[0] == "Devices [1]"
    assert lines[1] == ""
    assert lines[2] != ""


def test_bt_discover_preview_rows_are_all_the_same_length():
    # The actual bug this whole redesign was found fixing: draw_
    # centered_lines() centers each row INDEPENDENTLY, so rows of
    # differing length end up staggered at different x offsets once
    # rendered — confirmed live via screenshots. Every row must come
    # out exactly the same character count regardless of how long its
    # own name/status happen to be, so independent per-row centering
    # can't tell them apart.
    devices = [
        BluetoothDevice(id="AA", name="A", connected=False, paired=False),
        BluetoothDevice(id="BB", name="LongerDeviceName", connected=True, paired=True, rssi=-50),
    ]

    texts = _text_of(_bt_discover_preview_text(devices, None, _theme(), discovering=True))[1:]  # skip the title line
    texts = [t for t in texts if t]  # drop blank spacer lines

    assert len({len(text) for text in texts}) == 1


# ---------- _header_enter_hint_footer / _browsing_hint_footer ----------
# The level-1/level-2 discoverability hints, each their own separate
# preview_footer (a boxed-off strip preview.py draws across the bottom
# of the panel — see NavItem.preview_footer's own docstring) rather
# than a line mixed into preview_text above.

def test_header_enter_hint_footer_names_the_section():
    footer = _header_enter_hint_footer(_theme(), _cfg(), "networks")

    assert footer == [("[Enter] Browse networks", _theme()["urgent"])]


def test_header_enter_hint_footer_bluetooth_wording():
    footer = _header_enter_hint_footer(_theme(), _cfg(), "devices")

    assert footer == [("[Enter] Browse devices", _theme()["urgent"])]


def test_browsing_hint_footer_bluetooth_is_connect_scan_pairable_power_and_back():
    # No forget/hidden equivalent was asked for on the bluetooth side
    # (see connectivity.py's own module docstring on scope) — the hint
    # must not claim keys that do nothing there. Power/Pairable DO have
    # real bluetooth equivalents (bt_power_toggle/bt_pairable_toggle,
    # added 2026-08-16) — same two-line shape wifi's own "on" hint has,
    # Power on line 2 alongside Pairable (found live, Rafi's own
    # report: the "on" hint had no way to turn the radio back off).
    footer = _browsing_hint_footer(_theme(), _cfg(), "bluetooth", connected=False)

    assert footer == [
        ("[Enter] Connect   [S] Scan", _theme()["urgent"]),
        ("[A] Pairable   [P] Power   [Esc] Back", _theme()["urgent"]),
    ]


def test_browsing_hint_footer_bluetooth_says_disconnect_when_already_connected():
    footer = _browsing_hint_footer(_theme(), _cfg(), "bluetooth", connected=True)

    assert footer[0] == ("[Enter] Disconnect   [S] Scan", _theme()["urgent"])


def test_browsing_hint_footer_wifi_lists_every_active_wifi_only_key_when_known():
    # Regression: forget/connect-hidden/power-toggle shipped with no
    # discoverability hint at all until this was added — found live.
    footer = _browsing_hint_footer(_theme(), _cfg(), "wifi", connected=False, known=True)
    text = _text_of(footer)

    assert any("Connect" in line and "[Enter]" in line for line in text)
    assert any("Forget" in line and "[F]" in line for line in text)
    assert any("Hidden" in line and "[H]" in line for line in text)
    assert any("Power" in line and "[P]" in line for line in text)
    assert any("Scan" in line for line in text)
    assert any("Esc" in line for line in text)
    assert all(color == _theme()["urgent"] for _line, color in footer)


def test_browsing_hint_footer_has_item_false_omits_connect_and_forget():
    # The synthetic placeholder shown while browsing an empty section
    # (_empty_browsing_nav_item) has no selected network/device for
    # either to act on.
    footer = _browsing_hint_footer(_theme(), _cfg(), "wifi", connected=False, known=True, has_item=False)
    text = _text_of(footer)

    assert not any("Connect" in line for line in text)
    assert not any("Forget" in line for line in text)
    # Everything that doesn't need a selected item stays.
    assert any("Scan" in line for line in text)
    assert any("Hidden" in line for line in text)
    assert any("Power" in line for line in text)
    assert any("Esc" in line for line in text)


def test_browsing_hint_footer_wifi_narrows_to_just_power_when_radio_off():
    # Found live, Rafi's own report: with the radio off, Scan/Hidden/
    # Forget genuinely can't do anything (nothing to scan, no radio to
    # connect a hidden network on) — offering them was confusing, not
    # just unhelpful clutter.
    footer = _browsing_hint_footer(_theme(), _cfg(), "wifi", connected=False, known=True, powered=False)

    assert footer == [("[P] Power   [Esc] Back", _theme()["urgent"])]


def test_browsing_hint_footer_bluetooth_narrows_to_power_when_off():
    # Bluetooth's own adapter power toggle (2026-08-16) — same
    # narrowing wifi's own powered=False already had, its own key.
    footer = _browsing_hint_footer(_theme(), _cfg(), "bluetooth", connected=False, powered=False)

    assert footer == [("[P] Power   [Esc] Back", _theme()["urgent"])]


def test_browsing_hint_footer_wifi_omits_forget_when_not_known():
    # Found live, Rafi's own report: offering "Forget" on a network
    # that was never actually saved is misleading, not just redundant
    # — there's nothing there to delete.
    footer = _browsing_hint_footer(_theme(), _cfg(), "wifi", connected=False, known=False)
    text = _text_of(footer)

    assert not any("Forget" in line for line in text)
    # The rest of the hint must still be there, just without Forget.
    assert any("Connect" in line for line in text)
    assert any("Scan" in line for line in text)
    assert any("Hidden" in line for line in text)


def test_browsing_hint_footer_wifi_says_disconnect_when_already_connected():
    footer = _browsing_hint_footer(_theme(), _cfg(), "wifi", connected=True, known=True)
    text = _text_of(footer)

    assert any("Disconnect" in line for line in text)
    assert not any(line == "Connect" for line in text)


# ---------- _empty_browsing_nav_item ----------
# The synthetic placeholder browsing returns when the section has zero
# real items — found live, the missing piece behind an apparent
# "freeze" that turned out to be a stale-selection loop, not a hang.

def test_empty_browsing_nav_item_wifi_id_and_target_kind():
    item = _empty_browsing_nav_item("wifi", row=5, box=(0, 0, 40, 12), theme=_theme(), cfg=_cfg(), status=None,
                                     adapter_info=None, scanning=False)

    assert item.id == "connectivity:wifi:empty"
    assert item.target_kind == "wifi_empty"


def test_empty_browsing_nav_item_bluetooth_id_and_target_kind():
    item = _empty_browsing_nav_item("bluetooth", row=5, box=(0, 0, 40, 12), theme=_theme(), cfg=_cfg(), status=None,
                                     adapter_info=None, scanning=False)

    assert item.id == "connectivity:bt:empty"
    assert item.target_kind == "bt_empty"


def test_empty_browsing_nav_item_says_nothing_in_range():
    wifi_item = _empty_browsing_nav_item("wifi", row=5, box=(0, 0, 40, 12), theme=_theme(), cfg=_cfg(), status=None,
                                          adapter_info=None, scanning=False)
    bt_item = _empty_browsing_nav_item("bluetooth", row=5, box=(0, 0, 40, 12), theme=_theme(), cfg=_cfg(), status=None,
                                        adapter_info=None, scanning=False)

    assert _text_of(wifi_item.preview_text) == ["No networks in range"]
    assert _text_of(bt_item.preview_text) == ["No devices in range"]


def test_empty_browsing_nav_item_footer_has_no_connect_or_forget():
    item = _empty_browsing_nav_item("wifi", row=5, box=(0, 0, 40, 12), theme=_theme(), cfg=_cfg(), status=None,
                                     adapter_info=None, scanning=False)
    text = _text_of(item.preview_footer)

    assert not any("Connect" in line or "Forget" in line for line in text)
    assert any("Power" in line for line in text)


def test_empty_browsing_nav_item_wifi_carries_the_device_table():
    adapter = AdapterInfo(name="wlan0", powered=False)

    item = _empty_browsing_nav_item("wifi", row=5, box=(0, 0, 40, 12), theme=_theme(), cfg=_cfg(), status=None,
                                     adapter_info=adapter, scanning=False)

    assert [title for title, _rows in item.preview_data] == ["Device"]
    assert dict(item.preview_data[0][1][0])["Powered"] == "no"


def test_empty_browsing_nav_item_bluetooth_carries_the_device_table():
    # BluetoothAdapterInfo, added 2026-08-16 — bluetooth's own Device
    # table now survives into the empty-section placeholder exactly
    # like wifi's already does.
    adapter = BluetoothAdapterInfo(name="hci0", powered=False)

    item = _empty_browsing_nav_item("bluetooth", row=5, box=(0, 0, 40, 12), theme=_theme(), cfg=_cfg(), status=None,
                                     adapter_info=adapter, scanning=False)

    assert [title for title, _rows in item.preview_data] == ["Device"]
    assert dict(item.preview_data[0][1][0])["Powered"] == "no"


def test_empty_browsing_nav_item_shows_power_progress_when_pending():
    status = _FakeStatus(pending_keys={("wifi", "power")})

    item = _empty_browsing_nav_item("wifi", row=5, box=(0, 0, 40, 12), theme=_theme(), cfg=_cfg(), status=status,
                                     adapter_info=AdapterInfo(powered=False), scanning=False)

    assert "Powering on…" in _text_of(item.preview_text)


def test_empty_browsing_nav_item_bluetooth_power_progress_scoped_to_its_own_domain():
    # A wifi-scoped pending key must not leak into bluetooth's own
    # progress line — each section's power toggle is a separate
    # StatusWorker domain ("wifi" vs "bluetooth"), same pending_key
    # ("power") in both, so this is the one real place they could
    # bleed into each other if domain scoping were ever dropped.
    status = _FakeStatus(pending_keys={("wifi", "power")})

    item = _empty_browsing_nav_item("bluetooth", row=5, box=(0, 0, 40, 12), theme=_theme(), cfg=_cfg(), status=status,
                                     adapter_info=None, scanning=False)

    assert _text_of(item.preview_text) == ["No devices in range"]


def test_empty_browsing_nav_item_bluetooth_shows_its_own_power_progress():
    status = _FakeStatus(pending_keys={("bluetooth", "power")})

    item = _empty_browsing_nav_item("bluetooth", row=5, box=(0, 0, 40, 12), theme=_theme(), cfg=_cfg(), status=status,
                                     adapter_info=BluetoothAdapterInfo(powered=False), scanning=False)

    assert "Powering on…" in _text_of(item.preview_text)


def test_empty_browsing_nav_item_footer_narrows_when_radio_off():
    item = _empty_browsing_nav_item("wifi", row=5, box=(0, 0, 40, 12), theme=_theme(), cfg=_cfg(), status=None,
                                     adapter_info=AdapterInfo(powered=False), scanning=False)

    assert item.preview_footer == [("[P] Power   [Esc] Back", _theme()["urgent"])]


def test_empty_browsing_nav_item_bluetooth_footer_narrows_when_radio_off():
    item = _empty_browsing_nav_item("bluetooth", row=5, box=(0, 0, 40, 12), theme=_theme(), cfg=_cfg(), status=None,
                                     adapter_info=BluetoothAdapterInfo(powered=False), scanning=False)

    assert item.preview_footer == [("[P] Power   [Esc] Back", _theme()["urgent"])]


# ---------- _bt_adapter_info_table / _bt_preview_tables ----------
# Bluetooth's own mirror of _adapter_info_table/_wifi_preview_tables
# below (2026-08-16) — scoped to exactly what has a real action behind
# it (Powered/Pairable, both toggleable; Discovering, toggleable via
# the existing shared `scan` key) — see BluetoothAdapterInfo's own
# docstring for why Discoverable isn't included despite bluez exposing
# it, and _bt_preview_tables' own docstring for why there's no second
# "Connection" table the way WiFi has.

def test_bt_adapter_info_table_none_adapter_is_none():
    assert _bt_adapter_info_table(None, discovering=False) is None


def test_bt_adapter_info_table_includes_every_present_field():
    adapter = BluetoothAdapterInfo(name="hci0", address="D0:C6:37:61:24:D8", powered=True, pairable=True)

    columns = dict(_bt_adapter_info_table(adapter, discovering=False))

    assert columns["Name"] == "hci0"
    assert columns["Address"] == "D0:C6:37:61:24:D8"
    assert columns["Powered"] == "yes"
    assert columns["Pairable"] == "yes"


def test_bt_adapter_info_table_always_includes_discovering():
    adapter = BluetoothAdapterInfo(name="hci0")

    columns = dict(_bt_adapter_info_table(adapter, discovering=True))

    assert columns["Discovering"] == "yes"


def test_bt_preview_tables_is_device_only_no_connection_table():
    tables = _bt_preview_tables(BluetoothAdapterInfo(name="hci0"), discovering=False)

    assert [title for title, _rows in tables] == ["Device"]


# ---------- _adapter_info_table ----------
# impala's own separate, bordered "Device" panel — a NavItem.
# preview_table now (preview.py draws it as a real table box), not
# folded into preview_text as plain lines (a first pass did that,
# replaced after Rafi asked for impala's own boxed look specifically).

def test_adapter_info_table_none_adapter_is_none():
    assert _adapter_info_table(None, scanning=False) is None


def test_adapter_info_table_includes_every_present_field():
    adapter = AdapterInfo(
        name="wlan0", address="D0:C6:37:61:24:D4", model="Wireless 8265",
        vendor="Intel Corporation", supported_modes=["ad-hoc", "station", "ap"],
        mode="station", powered=True, state="connected",
    )

    columns = dict(_adapter_info_table(adapter, scanning=False))

    assert columns["Name"] == "wlan0"
    assert columns["Address"] == "D0:C6:37:61:24:D4"
    assert columns["Model"] == "Wireless 8265"
    assert columns["Vendor"] == "Intel Corporation"
    assert columns["Modes"] == "ad-hoc, station, ap"
    assert columns["Mode"] == "station"
    assert columns["State"] == "connected"
    assert columns["Powered"] == "yes"


def test_adapter_info_table_always_includes_scanning():
    # Scanning isn't a real AdapterInfo field (see AdapterInfo's own
    # docstring — it's the existing "wifi_scanning" domain, folded in
    # here rather than duplicated), so it's unconditional, unlike every
    # other column.
    adapter = AdapterInfo(name="wlan0")

    columns_scanning = dict(_adapter_info_table(adapter, scanning=True))
    columns_idle = dict(_adapter_info_table(adapter, scanning=False))

    assert columns_scanning["Scanning"] == "yes"
    assert columns_idle["Scanning"] == "no"


def test_adapter_info_table_omits_absent_fields():
    adapter = AdapterInfo(name="wlan0")

    columns = _adapter_info_table(adapter, scanning=False)
    labels = [label for label, _value in columns]

    assert labels == ["Name", "Scanning"]


def test_wifi_scan_preview_no_longer_carries_adapter_info():
    # Adapter info moved to its own preview_table (see above) —
    # preview_text stays plain networks-list content only now.
    lines = _text_of(_wifi_scan_preview_text([], None, _theme()))

    assert lines == ["No networks found"]


# ---------- _connection_diagnostics_table / _wifi_preview_tables ----------
# The second, stacked "Connection" table — live, negotiated details for
# whichever row is actually connected. Found live: a real AP in WPA2/
# WPA3 transition mode reports security="psk" on WifiNetwork (iwd's own
# Network.Type) but "WPA3-Personal" here (what actually got negotiated
# this session) — genuinely different information, not a duplicate.

def _flatten_table_rows(rows):
    return dict(item for row in rows for item in row)


def test_connection_diagnostics_table_includes_every_present_field():
    diagnostics = ConnectionDiagnostics(
        frequency=2427, channel=4, security="WPA3-Personal", rssi=-67, cipher="CCMP-128",
        tx_bitrate=144.4, rx_bitrate=130.0, ip_address="192.168.0.2",
    )

    columns = _flatten_table_rows(_connection_diagnostics_table(diagnostics))

    assert columns["Frequency"] == "2.43 GHz"
    assert columns["Channel"] == "4"
    assert columns["Live Security"] == "WPA3-Personal"
    assert columns["RSSI"] == "-67 dBm"
    assert columns["Cipher"] == "CCMP-128"
    assert columns["Tx Rate ↑"] == "144.4 Mb/s"
    assert columns["Rx Rate ↓"] == "130.0 Mb/s"
    assert columns["IP Address"] == "192.168.0.2"


def test_connection_diagnostics_table_is_a_single_row():
    diagnostics = ConnectionDiagnostics(frequency=2427, tx_bitrate=144.4, ip_address="192.168.0.2")

    rows = _connection_diagnostics_table(diagnostics)

    assert len(rows) == 1
    assert [label for label, _value in rows[0]] == ["Frequency", "Tx Rate ↑", "IP Address"]


def test_connection_diagnostics_table_normalizes_a_raw_backend_security_token():
    # NetworkManager's own get_connection_diagnostics() reports the
    # short classify_security() token ("sae"), not iwd's already-human
    # "WPA3-Personal" — both must read the same once displayed.
    diagnostics = ConnectionDiagnostics(security="sae")

    columns = _flatten_table_rows(_connection_diagnostics_table(diagnostics))

    assert columns["Live Security"] == "WPA3-Personal (SAE)"


def test_connection_diagnostics_table_omits_absent_fields():
    diagnostics = ConnectionDiagnostics(frequency=2427)

    rows = _connection_diagnostics_table(diagnostics)

    assert [label for row in rows for label, _value in row] == ["Frequency"]


def test_connection_diagnostics_table_omits_the_row_entirely_when_nothing_available():
    rows = _connection_diagnostics_table(ConnectionDiagnostics())

    assert rows == []


def test_wifi_preview_tables_always_includes_device():
    tables = _wifi_preview_tables(AdapterInfo(name="wlan0"), scanning=False)

    assert [title for title, _columns in tables] == ["Device"]


def test_wifi_preview_tables_adds_connection_when_connected_and_available():
    diagnostics = ConnectionDiagnostics(frequency=2427)

    tables = _wifi_preview_tables(AdapterInfo(name="wlan0"), scanning=False, diagnostics=diagnostics, connected=True)

    assert [title for title, _columns in tables] == ["Device", "Connection"]


def test_wifi_preview_tables_connection_title_names_the_ssid():
    # Found live, Rafi's own report: "Connection" alone, directly under
    # "Device", read as more device info at a glance rather than "here's
    # what's happening on THIS specific network."
    diagnostics = ConnectionDiagnostics(frequency=2427)

    tables = _wifi_preview_tables(
        AdapterInfo(name="wlan0"), scanning=False, diagnostics=diagnostics, connected=True, ssid="LSHK-WLAN",
    )

    assert tables[1][0] == "Connection - LSHK-WLAN"


def test_wifi_preview_tables_omits_connection_when_not_connected():
    # diagnostics is Station-wide, not per-network — only meaningful on
    # the row that's ACTUALLY connected, even if diagnostics data exists.
    diagnostics = ConnectionDiagnostics(frequency=2427)

    tables = _wifi_preview_tables(AdapterInfo(name="wlan0"), scanning=False, diagnostics=diagnostics, connected=False)

    assert [title for title, _columns in tables] == ["Device"]


def test_wifi_preview_tables_omits_connection_when_diagnostics_unavailable():
    tables = _wifi_preview_tables(AdapterInfo(name="wlan0"), scanning=False, diagnostics=None, connected=True)

    assert [title for title, _columns in tables] == ["Device"]


# ---------- forget-confirm: a Y/N sub-state of browsing, not its own mode_stack tier ----------

def test_forget_confirm_starts_unset():
    cancel_forget()  # reset module state by hand — same convention every other quartet's tests use

    assert is_confirming_forget() is False
    assert confirming_forget_ssid() is None


def test_request_forget_sets_the_pending_ssid():
    cancel_forget()
    request_forget("Home")

    assert is_confirming_forget() is True
    assert confirming_forget_ssid() == "Home"


def test_cancel_forget_clears_it():
    request_forget("Home")
    cancel_forget()

    assert is_confirming_forget() is False
    assert confirming_forget_ssid() is None


# ---------- hidden network entry: is_entering_hidden_ssid()'s own quartet ----------

def test_hidden_ssid_entry_starts_unset():
    cancel_hidden_ssid_entry()  # reset module state by hand

    assert is_entering_hidden_ssid() is False
    assert apply_hidden_ssid() is None


def test_start_hidden_ssid_entry_resets_any_previous_input():
    start_hidden_ssid_entry()
    handle_hidden_ssid_key(ord("X"))
    start_hidden_ssid_entry()  # a fresh start must not carry over "X"

    assert apply_hidden_ssid() == ""


def test_handle_hidden_ssid_key_types_printable_characters():
    start_hidden_ssid_entry()
    for ch in "MyNetwork":
        handle_hidden_ssid_key(ord(ch))

    assert apply_hidden_ssid() == "MyNetwork"


def test_handle_hidden_ssid_key_backspace_removes_last_character():
    start_hidden_ssid_entry()
    handle_hidden_ssid_key(ord("A"))
    handle_hidden_ssid_key(ord("B"))
    handle_hidden_ssid_key(127)  # backspace

    assert apply_hidden_ssid() == "A"


def test_handle_hidden_ssid_key_escape_returns_false():
    start_hidden_ssid_entry()

    assert handle_hidden_ssid_key(27) is False


def test_cancel_hidden_ssid_entry_clears_input_and_state():
    start_hidden_ssid_entry()
    handle_hidden_ssid_key(ord("X"))
    cancel_hidden_ssid_entry()

    assert is_entering_hidden_ssid() is False
    assert apply_hidden_ssid() is None


# ---------- _status_dot / _scanning_dot / header status segments ----------
# The Connectivity header row's own compact "[P]WR ● [S]CAN ○[...]"
# legend — real Powered/Scanning/(Bluetooth's own Pairable) state,
# drawn directly in the module's own box (not preview-exclusive), with
# a built-in key legend: the accent-colored letter in each word IS the
# actual keybind for that action. See CLAUDE/NOTES/design-decisions.md
# #module-self-sufficiency-vs-preview for why this belongs in-module.

def test_status_dot_true_is_filled_accent():
    char, color = _status_dot(_theme(), True)

    assert char == "●"
    assert color == (_theme()["accent"] | curses.A_BOLD)


def test_status_dot_false_is_dim_outline():
    char, color = _status_dot(_theme(), False)

    assert char == "○"
    assert color == (_theme()["text"] | curses.A_DIM)


def test_status_dot_none_reads_as_off_not_wrongly_on():
    # Adapter not polled yet — same "unknown reads as off" tolerance
    # _yes_no() already uses elsewhere in this module.
    char, color = _status_dot(_theme(), None)

    assert char == "○"
    assert color == (_theme()["text"] | curses.A_DIM)


def test_scanning_dot_false_is_dim_outline():
    char, color = _scanning_dot(_theme(), False)

    assert char == "○"
    assert color == (_theme()["text"] | curses.A_DIM)


def test_scanning_dot_true_is_filled_and_blinking():
    # The actual blink color alternates with wall-clock time (see
    # _pending_blink_style's own docstring) — not asserted exactly
    # here, just that scanning=True renders the FILLED glyph, unlike
    # _status_dot's own static on/off pair.
    char, _color = _scanning_dot(_theme(), True)

    assert char == "●"


def test_wifi_header_status_segments_spells_out_pwr_and_scan():
    segments = _wifi_header_status_segments(_theme(), powered=True, scanning=False, is_controllable=True)
    text = "".join(t for t, _c in segments)

    assert text == "  [PWR ●] [SCAN ○]"


def test_wifi_header_status_segments_accents_only_the_hotkey_letter():
    # "P" in PWR and "S" in SCAN are the actual wifi_power_toggle/scan
    # keybind letters (see defaults/config.toml) — accent-colored AND
    # bold, the rest of each word stays plain, un-bold text color.
    segments = _wifi_header_status_segments(_theme(), powered=True, scanning=False, is_controllable=True)
    by_text = {t: c for t, c in segments}

    assert by_text["P"] == (_theme()["accent"] | curses.A_BOLD)
    assert by_text["WR "] == _theme()["text"]
    assert by_text["S"] == (_theme()["accent"] | curses.A_BOLD)
    assert by_text["CAN "] == _theme()["text"]


def test_wifi_header_status_segments_dims_everything_until_controllable():
    # is_controllable=False (level-1 — the collapsed header row merely
    # selected/hovered, not real level-2 browsing) — Rafi's own call:
    # the whole legend reads as quiet background info until P/S/A can
    # actually be pressed, same instinct sessions.py's own dimmed
    # LOAD/SAVE/DEL/NAME already apply for their own expanded-or-not
    # state.
    segments = _wifi_header_status_segments(_theme(), powered=True, scanning=True, is_controllable=False)

    text = "".join(t for t, _c in segments)
    assert text == "  [PWR ●] [SCAN ●]"  # same text, dots included
    dim = _theme()["text"] | curses.A_DIM
    assert all(color == dim for _text, color in segments)


def test_bt_header_status_segments_spells_out_pwr_scan_pairable():
    segments = _bt_header_status_segments(_theme(), powered=True, scanning=False, pairable=True, is_controllable=True)
    text = "".join(t for t, _c in segments)

    assert text == "  [PWR ●] [SCAN ○] [PAIRABLE ●]"


def test_bt_header_status_segments_accents_the_a_in_pairable_not_the_p():
    # bt_pairable_toggle's own key is "a" — the SECOND character of
    # "PAIRABLE" gets the accent, not the word's first letter (Rafi's
    # own explicit call: "obarvi... A v PAIRABLE na accent").
    segments = _bt_header_status_segments(_theme(), powered=True, scanning=False, pairable=False, is_controllable=True)

    # The word splits into three separate segments: "P", "A", "IRABLE ".
    idx = [t for t, _c in segments].index("A")
    assert segments[idx] == ("A", _theme()["accent"] | curses.A_BOLD)
    assert segments[idx - 1] == ("P", _theme()["text"])
    assert segments[idx + 1] == ("IRABLE ", _theme()["text"])


def test_bt_header_status_segments_dims_everything_until_controllable():
    segments = _bt_header_status_segments(_theme(), powered=True, scanning=False, pairable=True, is_controllable=False)

    dim = _theme()["text"] | curses.A_DIM
    assert all(color == dim for _text, color in segments)


# ---------- flash_header_action / _is_header_action_flashing ----------
# A brief accent flash on just ONE bracket group ([PWR ...]/
# [SCAN ...]/[PAIRABLE ...]) — immediate confirmation a P/S/A keypress
# landed, distinct from StatusWorker.is_pending()'s own often-too-brief
# window (see flash_header_action's own docstring for the full "why").
#
# _flash_until is module-level state (same "no autouse fixture,
# resets by hand" convention _browsing_section's own tests already
# document) — cleared at the top of every test here, since a leftover
# deadline from an earlier test can otherwise still read as "flashing"
# under a freshly-mocked clock that happens to reuse the same value.

def test_flash_header_action_is_flashing_immediately_after(monkeypatch):
    connectivity_module._flash_until.clear()
    monkeypatch.setattr(connectivity_module.time, "monotonic", lambda: 100.0)

    flash_header_action("wifi", "scan")

    assert _is_header_action_flashing("wifi", "scan") is True


def test_flash_header_action_expires_after_the_flash_duration(monkeypatch):
    connectivity_module._flash_until.clear()
    now = [100.0]
    monkeypatch.setattr(connectivity_module.time, "monotonic", lambda: now[0])

    flash_header_action("wifi", "scan")
    now[0] += 10.0  # well past _FLASH_DURATION_SECONDS

    assert _is_header_action_flashing("wifi", "scan") is False


def test_flash_header_action_only_flashes_the_matching_section_and_action(monkeypatch):
    connectivity_module._flash_until.clear()
    now = [100.0]
    monkeypatch.setattr(connectivity_module.time, "monotonic", lambda: now[0])

    flash_header_action("wifi", "power")

    assert _is_header_action_flashing("wifi", "power") is True
    assert _is_header_action_flashing("wifi", "scan") is False  # different action
    assert _is_header_action_flashing("bluetooth", "power") is False  # different section


def test_is_header_action_flashing_false_when_never_flashed():
    connectivity_module._flash_until.clear()

    assert _is_header_action_flashing("bluetooth", "pairable") is False


# ---------- guarantee_scan_blink / is_scan_blink_guaranteed ----------
# A fixed-count reassurance blink, deliberately NOT tied to real
# is_scanning() — see guarantee_scan_blink's own docstring for why (a
# real scan completes too fast on real hardware for is_scanning() to
# ever visibly read True). _scan_guarantee_until is module-level state,
# same "clear by hand" convention as _flash_until's own tests above.

def test_guarantee_scan_blink_is_guaranteed_immediately_after(monkeypatch):
    connectivity_module._scan_guarantee_until.clear()
    monkeypatch.setattr(connectivity_module.time, "monotonic", lambda: 100.0)

    guarantee_scan_blink("wifi")

    assert is_scan_blink_guaranteed("wifi") is True


def test_guarantee_scan_blink_expires_after_scan_guarantee_seconds(monkeypatch):
    connectivity_module._scan_guarantee_until.clear()
    now = [100.0]
    monkeypatch.setattr(connectivity_module.time, "monotonic", lambda: now[0])

    guarantee_scan_blink("wifi")
    now[0] += connectivity_module.SCAN_GUARANTEE_SECONDS + 0.1

    assert is_scan_blink_guaranteed("wifi") is False


def test_guarantee_scan_blink_only_guarantees_the_matching_section(monkeypatch):
    connectivity_module._scan_guarantee_until.clear()
    monkeypatch.setattr(connectivity_module.time, "monotonic", lambda: 100.0)

    guarantee_scan_blink("wifi")

    assert is_scan_blink_guaranteed("wifi") is True
    assert is_scan_blink_guaranteed("bluetooth") is False


def test_is_scan_blink_guaranteed_false_when_never_triggered():
    connectivity_module._scan_guarantee_until.clear()

    assert is_scan_blink_guaranteed("bluetooth") is False


def test_wifi_header_status_segments_flashes_only_the_pressed_group():
    # power_flashing=True must recolor PWR's own group (brackets
    # included) accent, while SCAN's own group — same "[" text,
    # different position in the list — stays completely unaffected.
    # Checked by list position, not a text-keyed dict: both groups
    # legitimately share segments with identical text ("[", "]"), so a
    # dict would silently collide and hide exactly the bug this test
    # exists to catch.
    segments = _wifi_header_status_segments(
        _theme(), powered=True, scanning=False, is_controllable=True,
        power_flashing=True, scan_flashing=False,
    )
    flash_color = _theme()["accent"] | curses.A_BOLD

    # segments[0] is the leading "  ", segments[1:6] is PWR's own
    # 5-piece group ("[", "P", "WR ", dot, "]"), segments[6] is the
    # inter-group " ", segments[7:12] is SCAN's own group.
    assert all(color == flash_color for _text, color in segments[1:6])
    # SCAN's own dot specifically (segments[7:12][3]) — scanning=False
    # means it's the plain dim outline, never accent-colored under any
    # non-flashing circumstance, unlike the "S" hotkey letter right
    # next to it (already accent+bold even unflashed, so checking that
    # one wouldn't actually prove anything).
    scan_dot_text, scan_dot_color = segments[7:12][3]
    assert scan_dot_text == "○"
    assert scan_dot_color != flash_color


# ---------- _preview_available / _forget_hidden_hint_segments ----------
# The Connectivity box's own bottom-border-line fallback for wifi_
# forget/wifi_connect_hidden discoverability — CLAUDE/NOTES/design-
# decisions.md#module-self-sufficiency-vs-preview's own "mechanism #2"
# (a module checking whether preview exists in the layout at all and
# branching what it draws), first real consumer.

def test_preview_available_true_when_preview_box_present():
    cfg = SimpleNamespace(layout=SimpleNamespace(boxes=[
        SimpleNamespace(name="sidebar"), SimpleNamespace(name="preview"), SimpleNamespace(name="connectivity"),
    ]))

    assert _preview_available(cfg) is True


def test_preview_available_false_when_preview_box_absent():
    cfg = SimpleNamespace(layout=SimpleNamespace(boxes=[
        SimpleNamespace(name="sidebar"), SimpleNamespace(name="connectivity"),
    ]))

    assert _preview_available(cfg) is False


def test_preview_available_false_for_empty_layout():
    cfg = SimpleNamespace(layout=SimpleNamespace(boxes=[]))

    assert _preview_available(cfg) is False


def test_forget_hidden_hint_segments_spells_out_forget_and_hidden_connect():
    segments = _forget_hidden_hint_segments(_theme(), _cfg())
    text = "".join(t for t, _c in segments)

    assert text == "[F]orget   [H]idden connect"


def test_forget_hidden_hint_segments_bolds_only_the_mnemonic_letters():
    segments = _forget_hidden_hint_segments(_theme(), _cfg())
    by_text = {t: c for t, c in segments}
    urgent = _theme()["urgent"]

    assert by_text["F"] == (urgent | curses.A_BOLD)
    assert by_text["H"] == (urgent | curses.A_BOLD)
    assert by_text["]orget"] == urgent
    assert by_text["]idden connect"] == urgent
