"""Tests for battery.py. get_packs() is tested against a fake
/sys/class/power_supply tree built under tmp_path (see its own
docstring for why base_path is a parameter) — no monkeypatching of os
internals needed. aggregate() is tested directly against BatteryPack
values, including a real pair captured live off this session's own
sandbox (T480, BAT0+BAT1) — see aggregate()'s own docstring for why
energy-weighted vs plain-average genuinely disagree on real hardware,
not just in a contrived fixture. watch()'s "no packs" branch is a plain
fake-tree test like the rest of this file; its REAL select.poll()
behavior can only be verified against a real /sys tree (see its own
test's skipif) — this session's own sandbox happens to have one
(BAT0+BAT1), confirmed live before relying on it (see watch()'s own
docstring for the exact live-verified sysfs poll() quirk).
"""

import threading
import time

import pytest

from tuicc.battery import BatteryPack, aggregate, get_ac_online, get_packs, watch


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
    # This session's own real machine: an "AC" node plus TWO USB-C PD
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
    assert result["percent"] == round(100 * 18000000 / 22000000)
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
    assert result["percent"] == round((80 + 40) / 2)  # 60, plain average — NOT energy-weighted


def test_aggregate_real_t480_values_diverge_from_plain_average():
    """Captured live off this session's own sandbox: BAT0 at 6%/22.62Wh
    full, BAT1 at 15%/68.7Wh full. A plain percent average reads ~10-11%;
    the real combined charge (energy-weighted) is meaningfully different
    — this is the concrete case that motivated picking energy-weighting
    over swcc's plain average in the first place."""
    bat0 = BatteryPack(name="BAT0", percent=6, status="Not charging",
                        energy_now=1310000, energy_full=22620000)
    bat1 = BatteryPack(name="BAT1", percent=15, status="Charging",
                        energy_now=10500000, energy_full=68700000)

    result = aggregate([bat0, bat1])

    plain_average = round((6 + 15) / 2)
    energy_weighted = round(100 * (1310000 + 10500000) / (22620000 + 68700000))
    assert energy_weighted != plain_average  # the real machine actually exercises this divergence
    assert result["percent"] == energy_weighted


def test_aggregate_status_charging_wins_over_not_charging():
    # exactly this session's live machine state: BAT0 "Not charging"
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

def test_watch_with_no_packs_returns_immediately_without_yielding(tmp_path):
    stop_event = threading.Event()
    gen = watch(stop_event, base_path=str(tmp_path))
    with pytest.raises(StopIteration):
        next(gen)


def test_watch_stops_promptly_once_stop_event_is_set_before_starting(tmp_path):
    # no packs at all -> same "return immediately" path as above, but
    # explicitly with stop_event already set going in, guarding against
    # a version of watch() that would try to block first and check
    # stop_event only afterward.
    (tmp_path / "BAT0").mkdir()
    (tmp_path / "BAT0" / "capacity").write_text("50\n")
    (tmp_path / "BAT0" / "uevent").write_text("POWER_SUPPLY_CAPACITY=50\n")  # real BAT0 dirs always have this
    stop_event = threading.Event()
    stop_event.set()
    gen = watch(stop_event, base_path=str(tmp_path), poll_timeout=0.05)
    with pytest.raises(StopIteration):
        next(gen)


def test_watch_falls_back_to_yielding_even_without_a_real_kernel_event(tmp_path):
    # A plain regular file (not real sysfs) registered for POLLPRI|
    # POLLERR reliably just times out — confirmed live, this session —
    # so every yield seen here MUST come from the fallback path, not a
    # spurious "first poll" fire the way real sysfs has (see watch()'s
    # own docstring) — this is exactly what makes the fallback
    # deterministically testable without real hardware: the fix for
    # "charging start went undetected even after a long wait" (found
    # live, reported by the user) is that this generator no longer
    # depends SOLELY on a kernel notification that might just never
    # come on some driver/kernel.
    (tmp_path / "BAT0").mkdir()
    (tmp_path / "BAT0" / "capacity").write_text("50\n")
    (tmp_path / "BAT0" / "uevent").write_text("POWER_SUPPLY_CAPACITY=50\n")
    stop_event = threading.Event()
    gen = watch(stop_event, base_path=str(tmp_path), poll_timeout=0.05, fallback_seconds=0.1)

    start = time.monotonic()
    next(gen)  # first fallback yield
    elapsed = time.monotonic() - start
    stop_event.set()
    assert elapsed < 1.0  # generous upper bound — real target is ~0.1s, just guarding against "never"


@pytest.mark.skipif(not get_packs(), reason="needs real battery hardware (BAT*) present in /sys")
def test_watch_real_hardware_setup_does_not_crash_and_stop_event_terminates_it():
    """Live/integration check, not a full behavior test — this session's
    own sandbox happens to have real battery hardware (BAT0+BAT1), see
    this file's own module docstring for what was verified manually
    against it (the spurious-first-poll() quirk watch()'s own docstring
    describes). Can't simulate a real AC plug/unplug in a test, so this
    only confirms the select.poll() setup against the REAL /sys tree
    doesn't raise, and that stop_event reliably ends the generator
    within a bounded time even with nothing having actually changed.
    """
    stop_event = threading.Event()
    gen = watch(stop_event, poll_timeout=0.05)
    result = {}

    def run():
        try:
            next(gen)
            result["outcome"] = "yielded"
        except StopIteration:
            result["outcome"] = "stopped"
        except Exception as e:
            result["outcome"] = e

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    stop_event.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert result.get("outcome") == "stopped"  # nothing real changed during the test
