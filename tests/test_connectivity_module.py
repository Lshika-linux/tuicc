"""Tests for modules/connectivity.py's _build_rows — specifically the
None-vs-[] distinction VISION.md's R3 adds (a domain's last poll
erroring vs. it genuinely having nothing to report), since that's new,
previously-untested behavior. draw()/nav_items() themselves need a
real curses screen, left untested here same as every other module.
"""

from types import SimpleNamespace

from tuicc.modules.connectivity import _build_rows


def _ctx(wifi_networks=None, bluetooth_devices=None, wifi_error=None, bluetooth_error=None):
    return SimpleNamespace(
        wifi_networks=wifi_networks,
        bluetooth_devices=bluetooth_devices,
        wifi_error=wifi_error,
        bluetooth_error=bluetooth_error,
    )


def _kinds(rows):
    return [kind for kind, _payload in rows]


# ---------- wifi: error vs empty vs cold-start ----------

def test_wifi_error_row_when_networks_none_and_error_set():
    ctx = _ctx(wifi_networks=None, wifi_error="D-Bus unreachable")

    rows = _build_rows(ctx, box_h=20)

    assert ("error", "D-Bus unreachable") in rows


def test_wifi_cold_start_none_with_no_error_renders_as_empty_not_error():
    # Right after startup, before the first poll completes — nothing
    # has actually failed yet, must not look like an error.
    ctx = _ctx(wifi_networks=None, wifi_error=None)

    rows = _build_rows(ctx, box_h=20)

    assert ("empty", "(none found)") in rows
    assert "error" not in _kinds(rows)


def test_wifi_genuinely_empty_list_renders_as_empty_not_error():
    ctx = _ctx(wifi_networks=[], wifi_error=None)

    rows = _build_rows(ctx, box_h=20)

    assert ("empty", "(none found)") in rows
    assert "error" not in _kinds(rows)


# ---------- bluetooth: error vs empty vs cold-start ----------

def test_bluetooth_error_row_when_devices_none_and_error_set():
    ctx = _ctx(bluetooth_devices=None, bluetooth_error="bluetoothctl timeout")

    rows = _build_rows(ctx, box_h=20)

    assert ("error", "bluetoothctl timeout") in rows


def test_bluetooth_cold_start_none_with_no_error_renders_as_empty_not_error():
    ctx = _ctx(bluetooth_devices=None, bluetooth_error=None)

    rows = _build_rows(ctx, box_h=20)

    assert ("empty", "(none paired)") in rows
    assert "error" not in _kinds(rows)


def test_bluetooth_genuinely_empty_list_renders_as_empty_not_error():
    ctx = _ctx(bluetooth_devices=[], bluetooth_error=None)

    rows = _build_rows(ctx, box_h=20)

    assert ("empty", "(none paired)") in rows
    assert "error" not in _kinds(rows)


# ---------- both domains erroring independently ----------

def test_wifi_and_bluetooth_errors_are_independent():
    ctx = _ctx(
        wifi_networks=None, wifi_error="D-Bus unreachable",
        bluetooth_devices=[], bluetooth_error=None,
    )

    rows = _build_rows(ctx, box_h=20)

    assert ("error", "D-Bus unreachable") in rows
    assert ("empty", "(none paired)") in rows
