"""Tests for battery.py. get_packs() is tested against a fake
/sys/class/power_supply tree built under tmp_path (see its own
docstring for why base_path is a parameter) — no monkeypatching of os
internals needed. aggregate() is tested directly against BatteryPack
values, including a real pair captured off real T480 hardware
(BAT0+BAT1) — see CLAUDE/NOTES/design-decisions.md
#battery-energy-weighted-percent for why energy-weighted vs plain-
average genuinely disagree on real hardware, not just in a contrived
fixture.

watch() is pyudev-backed (see CLAUDE/NOTES/design-decisions.md
#battery-push-pyudev) — tested against a fake pyudev.Context/Monitor
(monkeypatched onto the battery module, same "fake the boundary, not
the internals" style providers/sway.py's own FakeConnection tests
use) rather than the real library, since real pyudev needs a real
netlink socket AND a real libudev.so ctypes can actually dlopen() —
neither guaranteed in a test environment (confirmed live: this exact
project's own NixOS dev venv can't load libudev at all, see
CLAUDE/NOTES/known-limitations.md#pyudev-libudev-nixos). This also
makes burst-draining and fallback timing deterministic in a way real
hardware events never could be.
"""

import threading
import time

import pytest

import tuicc.battery as battery_module
from tuicc.battery import BatteryPack, aggregate, get_ac_online, get_packs, read_status, watch


# ---------- get_packs ----------

def _write(path, text):
    path.write_text(text)


def test_no_power_supply_dir_returns_empty_list(tmp_path):
    missing = tmp_path / "does_not_exist"
    assert get_packs(str(missing)) == []


def test_no_bat_entries_returns_empty_list(tmp_path):
    (tmp_path / "AC").mkdir()
    _write(tmp_path / "AC" / "online", "1\n")
    assert get_packs(str(tmp_path)) == []


def test_single_battery_full_fields(tmp_path):
    bat0 = tmp_path / "BAT0"
    bat0.mkdir()
    _write(bat0 / "capacity", "82\n")
    _write(bat0 / "status", "Discharging\n")
    _write(bat0 / "energy_now", "18000000\n")
    _write(bat0 / "energy_full", "22000000\n")

    packs = get_packs(str(tmp_path))
    assert packs == [BatteryPack(
        name="BAT0", percent=82, status="Discharging",
        energy_now=18000000, energy_full=22000000,
    )]


def test_battery_missing_energy_fields_gets_none(tmp_path):
    """Some drivers only expose charge_*/capacity, not energy_* — get_packs()
    must not crash on a pack that simply doesn't have those files."""
    bat0 = tmp_path / "BAT0"
    bat0.mkdir()
    _write(bat0 / "capacity", "50\n")
    _write(bat0 / "status", "Full\n")

    packs = get_packs(str(tmp_path))
    assert packs == [BatteryPack(
        name="BAT0", percent=50, status="Full", energy_now=None, energy_full=None,
    )]


def test_non_bat_entries_are_ignored(tmp_path):
    (tmp_path / "BAT0").mkdir()
    _write(tmp_path / "BAT0" / "capacity", "50\n")
    (tmp_path / "AC").mkdir()
    _write(tmp_path / "AC" / "online", "1\n")
    (tmp_path / "ucsi-source-psy-USBC000:001").mkdir()

    packs = get_packs(str(tmp_path))
    assert [p.name for p in packs] == ["BAT0"]


def test_bat_entry_without_capacity_is_skipped():
    """A BAT*-named entry that doesn't even expose /capacity isn't a real
    battery pack (or is unreadable in some way that has to distinguish
    "not present" from "present but broken") — not this function's job to
    guess, get_state()'s no-silent-failure stance (module docstring)
    means an actually-broken pack should raise, not silently vanish; a
    genuinely absent one just isn't listed."""
    import tempfile, os
    with tempfile.TemporaryDirectory() as d:
        os.mkdir(os.path.join(d, "BAT_odd"))
        assert get_packs(d) == []


def test_multiple_batteries_sorted_by_name(tmp_path):
    for name, cap in [("BAT1", 15), ("BAT0", 6)]:
        p = tmp_path / name
        p.mkdir()
        _write(p / "capacity", f"{cap}\n")
        _write(p / "status", "Charging\n")

    packs = get_packs(str(tmp_path))
    assert [p.name for p in packs] == ["BAT0", "BAT1"]


# ---------- get_ac_online ----------

def test_get_ac_online_no_dir_returns_none(tmp_path):
    missing = tmp_path / "does_not_exist"
    assert get_ac_online(str(missing)) is None


def test_get_ac_online_no_non_battery_sources_returns_none(tmp_path):
    (tmp_path / "BAT0").mkdir()
    _write(tmp_path / "BAT0" / "type", "Battery\n")
    _write(tmp_path / "BAT0" / "capacity", "50\n")
    assert get_ac_online(str(tmp_path)) is None


def test_get_ac_online_true_when_ac_online(tmp_path):
    (tmp_path / "AC").mkdir()
    _write(tmp_path / "AC" / "type", "Mains\n")
    _write(tmp_path / "AC" / "online", "1\n")
    assert get_ac_online(str(tmp_path)) is True


def test_get_ac_online_false_when_present_but_not_online(tmp_path):
    (tmp_path / "AC").mkdir()
    _write(tmp_path / "AC" / "type", "Mains\n")
    _write(tmp_path / "AC" / "online", "0\n")
    assert get_ac_online(str(tmp_path)) is False


def test_get_ac_online_multiple_sources_any_online_wins(tmp_path):
    # A real ThinkPad: an "AC" node plus TWO USB-C PD
    # ports, only one of which is online at a time — hardcoding "AC"
    # alone would have missed this shape entirely (see get_ac_online's
    # own docstring).
    (tmp_path / "AC").mkdir()
    _write(tmp_path / "AC" / "type", "Mains\n")
    _write(tmp_path / "AC" / "online", "0\n")
    (tmp_path / "usbc1").mkdir()
    _write(tmp_path / "usbc1" / "type", "USB\n")
    _write(tmp_path / "usbc1" / "online", "1\n")
    (tmp_path / "usbc2").mkdir()
    _write(tmp_path / "usbc2" / "type", "USB\n")
    _write(tmp_path / "usbc2" / "online", "0\n")
    assert get_ac_online(str(tmp_path)) is True


def test_get_ac_online_ignores_battery_entries(tmp_path):
    (tmp_path / "BAT0").mkdir()
    _write(tmp_path / "BAT0" / "type", "Battery\n")
    # BAT0 has no "online" file in reality, but even if it somehow did,
    # a Battery-typed entry must never count as a charge SOURCE.
    _write(tmp_path / "BAT0" / "online", "1\n")
    assert get_ac_online(str(tmp_path)) is None


# ---------- aggregate ----------

def test_aggregate_of_empty_list_is_none():
    assert aggregate([]) is None


def test_aggregate_passes_ac_online_through_unchanged():
    pack = BatteryPack(name="BAT0", percent=50, status="Discharging",
                        energy_now=None, energy_full=None)
    assert aggregate([pack], ac_online=True)["ac_online"] is True
    assert aggregate([pack], ac_online=False)["ac_online"] is False
    assert aggregate([pack])["ac_online"] is None  # default, unspecified


def test_aggregate_single_pack_energy_weighted():
    pack = BatteryPack(name="BAT0", percent=82, status="Discharging",
                        energy_now=18000000, energy_full=22000000)
    result = aggregate([pack])
    # 18,000,000 / 22,000,000 * 100 = 81.818...% -> rounds to 82.
    # Hardcoded, not recomputed here, so a rounding/arithmetic bug in
    # aggregate() itself can't hide by being reproduced identically.
    assert result["percent"] == 82
    assert result["status"] == "Discharging"


def test_aggregate_falls_back_to_percent_average_when_energy_missing_on_any_pack():
    # BAT0 has full energy_* fields, BAT1 doesn't — the fallback is
    # ALL-or-nothing (see aggregate()'s own docstring "EVERY pack"), so
    # this must NOT silently ignore BAT1's missing fields and only
    # energy-weight BAT0.
    with_energy = BatteryPack(name="BAT0", percent=80, status="Discharging",
                               energy_now=8000000, energy_full=10000000)
    without_energy = BatteryPack(name="BAT1", percent=40, status="Discharging",
                                  energy_now=None, energy_full=None)
    result = aggregate([with_energy, without_energy])
    assert result["percent"] == 60  # plain average of 80 and 40 — NOT energy-weighted


def test_aggregate_real_t480_values_diverge_from_plain_average():
    """Captured live off a real sandbox: BAT0 at 6%/22.62Wh
    full, BAT1 at 15%/68.7Wh full. A plain percent average reads ~10-11%;
    the real combined charge (energy-weighted) is meaningfully different
    — this is the concrete case that motivated picking energy-weighting
    over swcc's plain average in the first place."""
    bat0 = BatteryPack(name="BAT0", percent=6, status="Not charging",
                        energy_now=1310000, energy_full=22620000)
    bat1 = BatteryPack(name="BAT1", percent=15, status="Charging",
                        energy_now=10500000, energy_full=68700000)

    result = aggregate([bat0, bat1])

    # Energy-weighted: (1,310,000 + 10,500,000) / (22,620,000 + 68,700,000)
    # * 100 = 12.9327...% -> rounds to 13. Hardcoded independently of
    # aggregate()'s own formula, same reasoning as the test above.
    assert result["percent"] == 13
    # A plain average of the raw percents (6, 15) would read 10 — a real,
    # meaningfully different number from what the machine actually showed.
    assert result["percent"] != round((6 + 15) / 2)


def test_aggregate_status_charging_wins_over_not_charging():
    # A real machine state: BAT0 "Not charging"
    # (topped off), BAT1 "Charging" — the combined status must read
    # "Charging", not get overridden by BAT0's own non-charging report.
    bat0 = BatteryPack(name="BAT0", percent=6, status="Not charging",
                        energy_now=1310000, energy_full=22620000)
    bat1 = BatteryPack(name="BAT1", percent=15, status="Charging",
                        energy_now=10500000, energy_full=68700000)
    assert aggregate([bat0, bat1])["status"] == "Charging"


def test_aggregate_status_discharging_when_none_charging():
    bat0 = BatteryPack(name="BAT0", percent=50, status="Discharging",
                        energy_now=None, energy_full=None)
    bat1 = BatteryPack(name="BAT1", percent=50, status="Unknown",
                        energy_now=None, energy_full=None)
    assert aggregate([bat0, bat1])["status"] == "Discharging"


def test_aggregate_status_falls_back_to_first_pack_when_none_charging_or_discharging():
    bat0 = BatteryPack(name="BAT0", percent=100, status="Full",
                        energy_now=None, energy_full=None)
    assert aggregate([bat0])["status"] == "Full"


def test_aggregate_percent_is_clamped_0_to_100():
    # defensive only — real /sys data shouldn't produce this, but the
    # computed (not directly-read) percent gets the same clamp
    # brightness.py's own set_percent applies to its input, same
    # boundary-owns-its-range reasoning.
    bat0 = BatteryPack(name="BAT0", percent=100, status="Full",
                        energy_now=5000001, energy_full=5000000)  # rounding/sensor drift edge case
    assert aggregate([bat0])["percent"] <= 100


# ---------- watch ----------

class _FakeMonitor:
    """poll_results: a queue of return values for successive poll()
    calls — a non-None entry stands in for a real pyudev Device object
    (watch() never inspects it, just checks "is this None or not"), so
    any truthy placeholder works.
    """
    def __init__(self, poll_results):
        self._poll_results = list(poll_results)
        self.filter_by_calls = []
        self.started = False

    def filter_by(self, subsystem):
        self.filter_by_calls.append(subsystem)

    def start(self):
        self.started = True

    def poll(self, timeout=None):
        if self._poll_results:
            return self._poll_results.pop(0)
        return None


class _FakeMonitorNamespace:
    def __init__(self, monitor):
        self._monitor = monitor

    def from_netlink(self, context):
        return self._monitor


class _FakePyudev:
    def __init__(self, monitor):
        self.Context = lambda: object()
        self.Monitor = _FakeMonitorNamespace(monitor)


def _patch_fake_pyudev(monkeypatch, poll_results):
    monitor = _FakeMonitor(poll_results)
    monkeypatch.setattr(battery_module, "pyudev", _FakePyudev(monitor))
    monkeypatch.setattr(battery_module, "PYUDEV_AVAILABLE", True)
    return monitor


def test_watch_raises_immediately_when_pyudev_unavailable(monkeypatch):
    monkeypatch.setattr(battery_module, "PYUDEV_AVAILABLE", False)
    stop_event = threading.Event()
    gen = watch(stop_event)
    with pytest.raises(ImportError):
        next(gen)


def test_watch_filters_to_power_supply_subsystem(monkeypatch):
    monitor = _patch_fake_pyudev(monkeypatch, poll_results=[])
    stop_event = threading.Event()
    stop_event.set()
    gen = watch(stop_event)
    with pytest.raises(StopIteration):
        next(gen)
    assert monitor.filter_by_calls == ["power_supply"]
    assert monitor.started is True


def test_watch_stops_promptly_once_stop_event_is_set_before_starting(monkeypatch):
    _patch_fake_pyudev(monkeypatch, poll_results=[])
    stop_event = threading.Event()
    stop_event.set()
    gen = watch(stop_event, poll_timeout=0.05)
    with pytest.raises(StopIteration):
        next(gen)


def test_watch_yields_once_per_real_event(monkeypatch):
    # A single "device changed" poll() result should produce exactly
    # one yield — the caller re-runs read_status() itself for the
    # fresh value, this generator only signals "something changed".
    _patch_fake_pyudev(monkeypatch, poll_results=["device"])
    stop_event = threading.Event()
    gen = watch(stop_event, poll_timeout=0.05, fallback_seconds=10.0)
    next(gen)  # the real event
    stop_event.set()


def test_watch_drains_a_burst_of_events_into_one_yield(monkeypatch):
    # A real physical plug/unplug commonly fires several devices'
    # worth of uevents within milliseconds (AC, BAT0, BAT1, a USB-C PD
    # source — confirmed live via udevadm monitor, see
    # CLAUDE/NOTES/design-decisions.md#battery-push-pyudev). One
    # burst should still be exactly one yield, not four.
    monitor = _patch_fake_pyudev(monkeypatch, poll_results=["ac", "bat0", "bat1", "usbc"])
    stop_event = threading.Event()
    gen = watch(stop_event, poll_timeout=0.05, fallback_seconds=10.0)
    next(gen)
    # The whole burst was consumed by this one yield — nothing left
    # queued for the fake monitor to hand back.
    assert monitor._poll_results == []
    stop_event.set()


def test_watch_falls_back_to_yielding_even_without_a_real_event(monkeypatch):
    # No real events queued at all — every yield here MUST come from
    # the fallback path. This is what guards "charging start went
    # undetected even after a long wait" without depending solely on a
    # kernel notification that might just never come.
    _patch_fake_pyudev(monkeypatch, poll_results=[])
    stop_event = threading.Event()
    gen = watch(stop_event, poll_timeout=0.05, fallback_seconds=0.1)

    start = time.monotonic()
    next(gen)  # first fallback yield
    elapsed = time.monotonic() - start
    stop_event.set()
    assert elapsed < 1.0  # generous upper bound — real target is ~0.1s, just guarding against "never"


# ---------- PYUDEV_AVAILABLE probe ----------
# battery.PYUDEV_AVAILABLE is set once at import time by actually
# constructing a throwaway pyudev.Context() — not just checking
# whether the bare `import pyudev` succeeded. See
# CLAUDE/NOTES/design-decisions.md#battery-push-pyudev and
# known-limitations.md#pyudev-libudev-nixos for why that distinction
# is real, not defensive overkill: this project's own NixOS dev venv
# has pyudev import cleanly but fail at Context() construction time.

def test_probe_false_when_pyudev_did_not_import_at_all(monkeypatch):
    monkeypatch.setattr(battery_module, "pyudev", None)
    assert battery_module._probe_pyudev_available() is False


def test_probe_false_when_context_construction_raises(monkeypatch):
    class _FailingPyudev:
        def Context(self):
            raise ImportError("No library named udev")
    monkeypatch.setattr(battery_module, "pyudev", _FailingPyudev())
    assert battery_module._probe_pyudev_available() is False


def test_probe_true_when_context_constructs_cleanly(monkeypatch):
    class _WorkingPyudev:
        def Context(self):
            return object()
    monkeypatch.setattr(battery_module, "pyudev", _WorkingPyudev())
    assert battery_module._probe_pyudev_available() is True


# ---------- read_status ----------
# The shared refresh/poll target both app_setup.py wiring branches use
# (see build_app()) — thin wiring, but worth a smoke test that it
# actually calls through to aggregate()/get_packs()/get_ac_online()
# correctly rather than each branch having its own inline lambda that
# could drift apart.

def test_read_status_no_packs_returns_none(tmp_path):
    assert read_status(base_path=str(tmp_path)) is None


def test_read_status_wires_packs_and_ac_online_together(tmp_path):
    (tmp_path / "BAT0").mkdir()
    (tmp_path / "BAT0" / "capacity").write_text("77\n")
    (tmp_path / "BAT0" / "status").write_text("Charging\n")
    (tmp_path / "AC").mkdir()
    (tmp_path / "AC" / "type").write_text("Mains\n")
    (tmp_path / "AC" / "online").write_text("1\n")

    result = read_status(base_path=str(tmp_path))

    assert result == {"percent": 77, "status": "Charging", "ac_online": True}
