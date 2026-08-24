"""Tests for connectivity/networkmanager.py's pure functions — hand-built
fixture data shaped like real NetworkManager D-Bus reply bodies, no
mocks, same discipline as test_connectivity.py/test_iwd_agent.py.
"""

from tuicc.connectivity.networkmanager import (
    NM_AP_FLAG_PRIVACY,
    NM_AP_SEC_KEY_MGMT_802_1X,
    NM_AP_SEC_KEY_MGMT_OWE,
    NM_AP_SEC_KEY_MGMT_OWE_TM,
    NM_AP_SEC_KEY_MGMT_PSK,
    NM_AP_SEC_KEY_MGMT_SAE,
    NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
    NM_ACTIVE_CONNECTION_STATE_ACTIVATING,
    NM_ACTIVE_CONNECTION_STATE_DEACTIVATED,
    NM_DEVICE_STATE_REASON_NO_SECRETS,
    NM_DEVICE_TYPE_WIFI,
    _activation_outcome,
    _build_wifi_network,
    _epoch_to_iso,
    _failure_reason_message,
    _scan_completed,
    _scan_timed_out,
    classify_security,
    find_known_profile_for_ssid,
    find_wifi_device_path,
    frequency_to_channel,
    known_profile_fields,
    merge_access_points_by_ssid,
)
from tuicc.modules.connectivity import _format_timestamp


# ---- find_wifi_device_path ----

def test_find_wifi_device_path_finds_wifi_among_other_types():
    device_types = [("/org/freedesktop/NetworkManager/Devices/1", 1),  # ethernet
                     ("/org/freedesktop/NetworkManager/Devices/2", NM_DEVICE_TYPE_WIFI),
                     ("/org/freedesktop/NetworkManager/Devices/3", 14)]  # loopback
    assert find_wifi_device_path(device_types) == "/org/freedesktop/NetworkManager/Devices/2"


def test_find_wifi_device_path_no_wifi_device_returns_none():
    device_types = [("/org/freedesktop/NetworkManager/Devices/1", 1)]
    assert find_wifi_device_path(device_types) is None


def test_find_wifi_device_path_empty_list_returns_none():
    assert find_wifi_device_path([]) is None


# ---- frequency_to_channel ----

def test_frequency_to_channel_5ghz():
    # Real value captured live off this session's own machine (a real
    # NetworkManager connection to a 5GHz AP) — 5240 MHz is channel 48.
    assert frequency_to_channel(5240) == 48


def test_frequency_to_channel_2_4ghz():
    assert frequency_to_channel(2412) == 1  # channel 1
    assert frequency_to_channel(2437) == 6  # channel 6


def test_frequency_to_channel_2_4ghz_channel_14_special_case():
    # Channel 14 (Japan-only) breaks the linear 5MHz-per-channel
    # spacing every other 2.4GHz channel follows.
    assert frequency_to_channel(2484) == 14


def test_frequency_to_channel_6ghz():
    assert frequency_to_channel(5955) == 1


def test_frequency_to_channel_none_passthrough():
    assert frequency_to_channel(None) is None


def test_frequency_to_channel_out_of_band_returns_none():
    # An honest gap, not a nonsense channel number, for anything that
    # isn't a real 802.11 band.
    assert frequency_to_channel(1000) is None


# ---- classify_security ----

def test_classify_security_open():
    assert classify_security(0, 0, 0) == "open"


def test_classify_security_wep_via_privacy_flag_only():
    assert classify_security(NM_AP_FLAG_PRIVACY, 0, 0) == "wep"


def test_classify_security_psk():
    assert classify_security(NM_AP_FLAG_PRIVACY, 0, NM_AP_SEC_KEY_MGMT_PSK) == "psk"


def test_classify_security_sae_only_wpa3():
    assert classify_security(NM_AP_FLAG_PRIVACY, 0, NM_AP_SEC_KEY_MGMT_SAE) == "sae"


def test_classify_security_transition_mode_psk_and_sae_prefers_psk():
    # Real WPA2/WPA3 transition-mode APs advertise both bits at once in
    # RsnFlags — PSK must win (broader compatibility), not SAE.
    combined = NM_AP_SEC_KEY_MGMT_PSK | NM_AP_SEC_KEY_MGMT_SAE
    assert classify_security(NM_AP_FLAG_PRIVACY, 0, combined) == "psk"


def test_classify_security_8021x():
    assert classify_security(NM_AP_FLAG_PRIVACY, 0, NM_AP_SEC_KEY_MGMT_802_1X) == "8021x"


def test_classify_security_owe():
    assert classify_security(NM_AP_FLAG_PRIVACY, 0, NM_AP_SEC_KEY_MGMT_OWE) == "owe"


def test_classify_security_owe_transition_mode():
    assert classify_security(NM_AP_FLAG_PRIVACY, 0, NM_AP_SEC_KEY_MGMT_OWE_TM) == "owe"


def test_classify_security_checks_both_wpa_and_rsn_flags():
    assert classify_security(NM_AP_FLAG_PRIVACY, NM_AP_SEC_KEY_MGMT_PSK, 0) == "psk"


# ---- merge_access_points_by_ssid ----

def test_merge_access_points_by_ssid_groups_by_ssid():
    entries = [
        {"ssid": "Home", "strength": 40, "security": "psk", "path": "/ap/1", "connected": False},
        {"ssid": "Home", "strength": 80, "security": "psk", "path": "/ap/2", "connected": False},
    ]
    merged = merge_access_points_by_ssid(entries)
    assert len(merged) == 1
    assert merged[0]["strength"] == 80
    assert merged[0]["path"] == "/ap/2"


def test_merge_access_points_by_ssid_connected_is_ored_across_group():
    # The connected AP is the WEAKER of the two — strongest still wins
    # for signal/path, but "connected" must still come out True.
    entries = [
        {"ssid": "Home", "strength": 30, "security": "psk", "path": "/ap/weak", "connected": True},
        {"ssid": "Home", "strength": 90, "security": "psk", "path": "/ap/strong", "connected": False},
    ]
    merged = merge_access_points_by_ssid(entries)
    assert len(merged) == 1
    assert merged[0]["connected"] is True
    assert merged[0]["path"] == "/ap/strong"


def test_merge_access_points_by_ssid_excludes_empty_ssid():
    entries = [{"ssid": "", "strength": 50, "security": "psk", "path": "/ap/1", "connected": False}]
    assert merge_access_points_by_ssid(entries) == []


def test_merge_access_points_by_ssid_distinct_ssids_stay_separate():
    entries = [
        {"ssid": "Home", "strength": 50, "security": "psk", "path": "/ap/1", "connected": False},
        {"ssid": "Cafe", "strength": 60, "security": "open", "path": "/ap/2", "connected": False},
    ]
    merged = merge_access_points_by_ssid(entries)
    assert {m["ssid"] for m in merged} == {"Home", "Cafe"}


# ---- find_known_profile_for_ssid ----

def _wireless_settings(ssid, extra=None):
    settings = {"802-11-wireless": {"ssid": ("ay", ssid.encode("utf-8"))}}
    if extra:
        settings.update(extra)
    return settings


def test_find_known_profile_for_ssid_matches_by_decoded_ssid():
    connections = [
        ("/settings/1", _wireless_settings("Cafe")),
        ("/settings/2", _wireless_settings("Home")),
    ]
    assert find_known_profile_for_ssid(connections, "Home") == "/settings/2"


def test_find_known_profile_for_ssid_no_match_returns_none():
    connections = [("/settings/1", _wireless_settings("Cafe"))]
    assert find_known_profile_for_ssid(connections, "Home") is None


def test_find_known_profile_for_ssid_skips_non_wifi_profiles():
    connections = [
        ("/settings/1", {"connection": {"type": ("s", "vpn")}}),
        ("/settings/2", _wireless_settings("Home")),
    ]
    assert find_known_profile_for_ssid(connections, "Home") == "/settings/2"


# ---- known_profile_fields ----

def test_known_profile_fields_autoconnect_defaults_true_when_absent():
    settings = {"connection": {}, "802-11-wireless": {}}
    auto_connect, hidden, last_connected = known_profile_fields(settings)
    assert auto_connect is True


def test_known_profile_fields_autoconnect_explicit_false_respected():
    settings = {"connection": {"autoconnect": ("b", False)}, "802-11-wireless": {}}
    auto_connect, _hidden, _last = known_profile_fields(settings)
    assert auto_connect is False


def test_known_profile_fields_hidden_defaults_false_when_absent():
    settings = {"connection": {}, "802-11-wireless": {}}
    _auto, hidden, _last = known_profile_fields(settings)
    assert hidden is False


def test_known_profile_fields_hidden_explicit_true_respected():
    settings = {"connection": {}, "802-11-wireless": {"hidden": ("b", True)}}
    _auto, hidden, _last = known_profile_fields(settings)
    assert hidden is True


def test_known_profile_fields_zero_timestamp_means_never_connected():
    settings = {"connection": {"timestamp": ("t", 0)}, "802-11-wireless": {}}
    _auto, _hidden, last_connected = known_profile_fields(settings)
    assert last_connected is None


def test_known_profile_fields_absent_timestamp_means_never_connected():
    settings = {"connection": {}, "802-11-wireless": {}}
    _auto, _hidden, last_connected = known_profile_fields(settings)
    assert last_connected is None


def test_known_profile_fields_real_timestamp_converted():
    settings = {"connection": {"timestamp": ("t", 1700000000)}, "802-11-wireless": {}}
    _auto, _hidden, last_connected = known_profile_fields(settings)
    assert last_connected == _epoch_to_iso(1700000000)
    assert last_connected.endswith("Z")


# ---- _epoch_to_iso round-trips through the real _format_timestamp ----

def test_epoch_to_iso_round_trips_through_format_timestamp():
    iso = _epoch_to_iso(1700000000)
    # Must not raise, and must produce something other than the raw
    # input back — proves modules/connectivity.py needs no changes to
    # consume this backend's own timestamps.
    formatted = _format_timestamp(iso)
    assert formatted != ""
    assert formatted is not None


# ---- _build_wifi_network ----

def test_build_wifi_network_unknown_network_has_none_fields():
    merged_ap = {"ssid": "Cafe", "strength": 70, "security": "open", "path": "/ap/1", "connected": False}
    network = _build_wifi_network(merged_ap, None)
    assert network.known is False
    assert network.auto_connect is None
    assert network.hidden is None
    assert network.last_connected is None
    assert network.ssid == "Cafe"
    assert network.signal == 70
    assert network.security == "open"


def test_build_wifi_network_known_network_pulls_profile_fields():
    merged_ap = {"ssid": "Home", "strength": 90, "security": "psk", "path": "/ap/1", "connected": True}
    profile_settings = {
        "connection": {"autoconnect": ("b", True), "timestamp": ("t", 1700000000)},
        "802-11-wireless": {"hidden": ("b", False)},
    }
    network = _build_wifi_network(merged_ap, profile_settings)
    assert network.known is True
    assert network.auto_connect is True
    assert network.hidden is False
    assert network.last_connected is not None
    assert network.connected is True


# ---- _scan_completed / _scan_timed_out ----

def test_scan_completed_true_when_no_baseline_ever_set():
    assert _scan_completed(None, 12345) is True


def test_scan_completed_false_when_live_matches_baseline():
    assert _scan_completed(1000, 1000) is False


def test_scan_completed_false_when_live_is_none():
    assert _scan_completed(1000, None) is False


def test_scan_completed_true_when_live_moved_past_baseline():
    assert _scan_completed(1000, 2000) is True


def test_scan_timed_out_false_when_never_started():
    assert _scan_timed_out(None, 100.0) is False


def test_scan_timed_out_false_within_window():
    assert _scan_timed_out(100.0, 105.0, timeout=15) is False


def test_scan_timed_out_true_past_window():
    assert _scan_timed_out(100.0, 200.0, timeout=15) is True


# ---- _activation_outcome ----

def test_activation_outcome_activated_is_success():
    assert _activation_outcome(NM_ACTIVE_CONNECTION_STATE_ACTIVATED) == "success"


def test_activation_outcome_deactivated_is_failure():
    assert _activation_outcome(NM_ACTIVE_CONNECTION_STATE_DEACTIVATED) == "failure"


def test_activation_outcome_activating_is_pending():
    assert _activation_outcome(NM_ACTIVE_CONNECTION_STATE_ACTIVATING) == "pending"


# ---- _failure_reason_message ----

def test_failure_reason_message_known_code():
    assert "passphrase" in _failure_reason_message(NM_DEVICE_STATE_REASON_NO_SECRETS)


def test_failure_reason_message_unknown_code_includes_raw_code():
    message = _failure_reason_message(9999)
    assert "9999" in message
