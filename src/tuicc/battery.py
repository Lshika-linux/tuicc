"""Battery status via /sys/class/power_supply/BAT* — data source for the
`bars` module's BAT bar. A single, plain module (not a registry package
like audio/connectivity), same reasoning brightness.py's own docstring
gives for brightnessctl: /sys is the only reasonable source.

Battery *reading* only — CLAUDE/VISION.md's R6 (system monitor) owns
per-window CPU/RAM aggregation separately.

No internal try/except on capacity/status/energy_now/energy_full once a
BAT* directory is confirmed to exist — same no-silent-failure stance as
audio/connectivity's backends: a real read failure on a battery that IS
there must propagate, not vanish into a wrong number. Only "no BAT*
directories at all" comes back as an empty list (see get_packs()).

watch() (below) is a second, independent, push-based way of noticing a
change, for latency polling can't match — pyudev-backed (netlink
`power_supply` subsystem monitoring), after a sysfs select.poll()-based
first attempt was confirmed live to never fire on a real charger
plug/unplug. See CLAUDE/NOTES/design-decisions.md
#battery-push-pyudev for the live udevadm confirmation this is built
on, and CLAUDE/NOTES/known-limitations.md#battery-push-unreliable for
the sysfs attempt this replaced. app_setup.py checks PYUDEV_AVAILABLE
before wiring watch() in at all — if pyudev somehow isn't installed,
battery falls back to being a plain StatusWorker poll Domain exactly
like before this existed, not a silent battery-reading blackout.
"""

import os
import time
from dataclasses import dataclass

try:
    import pyudev
except ImportError:
    pyudev = None


def _probe_pyudev_available() -> bool:
    """PYUDEV_AVAILABLE isn't just "did the pyudev package import" —
    pyudev.Context() lazily dlopen()s the real libudev.so via ctypes
    the first time it's actually constructed, which can fail even when
    the Python package itself installed fine. Confirmed live in this
    exact codebase's own NixOS dev environment: pip-installed pyudev
    imports cleanly, but Context() raises ImportError("No library
    named udev") — NixOS doesn't expose libraries at the standard FHS
    paths ctypes.util.find_library() searches, unlike most other
    distros. Probed once, here, at import time, rather than only
    failing later inside watch() itself — app_setup.py's own push-vs-
    pull wiring decision needs to know this UP FRONT, not discover it
    only once a PushWorker thread is already running.
    """
    if pyudev is None:
        return False
    try:
        pyudev.Context()
        return True
    except Exception:
        return False


PYUDEV_AVAILABLE = _probe_pyudev_available()

POWER_SUPPLY_PATH = "/sys/class/power_supply"


@dataclass
class BatteryPack:
    name: str  # "BAT0", "BAT1", ...
    percent: int  # 0-100, this pack's own /capacity reading — always present
    status: str  # whatever the kernel reports, raw ("Charging"/"Discharging"/
                 # "Not charging"/"Full"/"Unknown") — aggregate() is the only
                 # place that interprets it
    energy_now: int | None  # µWh, None if this pack's driver doesn't expose
                             # energy_* at all (some only expose charge_*/
                             # capacity) — aggregate() falls back when this
                             # happens on ANY pack
    energy_full: int | None


def _read_int(path: str) -> int | None:
    """None if the file doesn't exist at all (this pack's driver genuinely
    doesn't expose this attribute) — distinct from a real read failure
    (permission, I/O error), which raises and propagates rather than
    silently returning a fallback (see module docstring).
    """
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return int(f.read().strip())


def _read_str(path: str, default: str) -> str:
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return f.read().strip()


def get_packs(base_path: str = POWER_SUPPLY_PATH) -> list[BatteryPack]:
    """Every BAT* the kernel exposes under base_path, sorted by name for a
    stable order. An empty list is a normal answer (desktop, no battery),
    not an error — the caller (bars module) just draws no BAT bar, same
    "degraded, not broken" tolerance the Provider contract's optional
    methods get.

    base_path is a parameter (not a hardcoded read of POWER_SUPPLY_PATH)
    so tests can point this at a fake tree instead of monkeypatching os
    internals — mirrors how providers/sway.py's own tests inject a fake
    connection rather than patching subprocess globally.
    """
    if not os.path.isdir(base_path):
        return []
    names = sorted(n for n in os.listdir(base_path) if n.startswith("BAT"))
    packs = []
    for name in names:
        base = os.path.join(base_path, name)
        capacity = _read_int(os.path.join(base, "capacity"))
        if capacity is None:
            continue  # this BAT* entry doesn't actually expose a capacity — not a real pack
        packs.append(BatteryPack(
            name=name,
            percent=capacity,
            status=_read_str(os.path.join(base, "status"), "Unknown"),
            energy_now=_read_int(os.path.join(base, "energy_now")),
            energy_full=_read_int(os.path.join(base, "energy_full")),
        ))
    return packs


def get_ac_online(base_path: str = POWER_SUPPLY_PATH) -> bool | None:
    """True if any non-battery power source (AC mains, a USB-C PD port,
    ...) currently reports itself online — the "is a charger physically
    connected" signal, distinct from any BAT pack's own `status` field.
    See CLAUDE/NOTES/design-decisions.md#battery-ac-online-separate for
    why this needs to be separate and why every entry under base_path
    is checked rather than a hardcoded "AC" name.

    None (not False) if this machine exposes no non-battery power
    sources at all — distinct from "checked, none are online", same
    None-vs-[] discipline as get_packs().
    """
    if not os.path.isdir(base_path):
        return None
    online_values = []
    for name in sorted(os.listdir(base_path)):
        base = os.path.join(base_path, name)
        if _read_str(os.path.join(base, "type"), "") == "Battery":
            continue
        online = _read_int(os.path.join(base, "online"))
        if online is not None:
            online_values.append(online)
    if not online_values:
        return None
    return any(value == 1 for value in online_values)


def aggregate(packs: list[BatteryPack], ac_online: bool | None = None) -> dict | None:
    """One combined {"percent", "status", "ac_online"} view across every
    pack — percent energy-weighted when possible, status pack-OR'd,
    ac_online passed through unchanged. See
    CLAUDE/NOTES/design-decisions.md#battery-energy-weighted-percent.
    None (not a dict) if `packs` is empty — "no battery", not "0%".
    """
    if not packs:
        return None

    if all(p.energy_now is not None and p.energy_full is not None and p.energy_full > 0
           for p in packs):
        total_now = sum(p.energy_now for p in packs)
        total_full = sum(p.energy_full for p in packs)
        percent = round(100 * total_now / total_full)
    else:
        percent = round(sum(p.percent for p in packs) / len(packs))
    percent = max(0, min(100, percent))

    statuses = {p.status for p in packs}
    if "Charging" in statuses:
        status = "Charging"
    elif "Discharging" in statuses:
        status = "Discharging"
    else:
        status = packs[0].status

    return {"percent": percent, "status": status, "ac_online": ac_online}


def read_status(base_path: str = POWER_SUPPLY_PATH) -> dict | None:
    """The one-call combined reading — aggregate(get_packs(),
    get_ac_online()) — pulled out as its own function since it's now
    used as both a plain StatusWorker poll target (the PYUDEV_AVAILABLE
    fallback) and a PushWorker refresh target (app_setup.py wires
    whichever applies), rather than duplicating the same lambda twice.
    """
    return aggregate(get_packs(base_path), get_ac_online(base_path))


def watch(stop_event, poll_timeout: float = 1.0, fallback_seconds: float = 2.0):
    """Generator: blocks on pyudev's netlink monitor for `power_supply`
    subsystem uevents (AC/BAT* change events), yielding once per
    detected event — draining any already-queued burst first, since a
    single physical plug/unplug commonly fires several devices' worth
    of events within milliseconds (AC, BAT0, BAT1, a USB-C PD source,
    ... all at once — confirmed live via `udevadm monitor
    --subsystem-match=power_supply`, see CLAUDE/NOTES/
    design-decisions.md#battery-push-pyudev) — or every fallback_seconds
    regardless, same "never silently stale" ceiling the earlier sysfs
    attempt had. The caller still re-runs read_status() itself for the
    fresh value either way; this only signals "something changed."

    Raises ImportError immediately if pyudev isn't actually installed
    (PYUDEV_AVAILABLE is False) rather than silently doing nothing —
    app_setup.py checks that flag BEFORE ever wiring this in as a
    PushDomain, so reaching here with pyudev missing would be a real
    wiring bug, not a runtime condition to degrade from quietly.
    """
    if not PYUDEV_AVAILABLE:
        raise ImportError("pyudev is not installed — battery.watch() requires it")

    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by(subsystem="power_supply")
    monitor.start()

    poll_timeout = min(poll_timeout, fallback_seconds)
    last_yield = time.monotonic()
    while not stop_event.is_set():
        device = monitor.poll(timeout=poll_timeout)
        if stop_event.is_set():
            break
        now = time.monotonic()
        if device is not None:
            # Drain the rest of this burst without blocking further —
            # one refresh covers whatever just happened, no need to
            # yield once per device in the same physical event.
            while monitor.poll(timeout=0) is not None:
                pass
            last_yield = now
            yield
        elif now - last_yield >= fallback_seconds:
            last_yield = now
            yield
