"""Battery status via /sys/class/power_supply/BAT* — data source for the
`bars` module's BAT bar. A single, plain module (not a registry package
like audio/connectivity), same reasoning brightness.py's own docstring
gives for brightnessctl: /sys is the only reasonable source, there's
nothing to choose between.

This is battery *reading* only — VISION.md's R6 (system monitor) still
owns the much harder per-window CPU/RAM aggregation. Battery percent is
a standalone, low-risk read that doesn't overlap with R6's actual hard
part, so it ships ahead of R6 rather than waiting for it or duplicating
its scope.

No internal try/except on the reads that matter (capacity/status/
energy_now/energy_full) once a BAT* directory is confirmed to exist —
same no-silent-failure stance as audio/connectivity's own backends: a
real read failure (permission, I/O error) on a battery that IS there
must propagate to StatusWorker's poll wrapper, not vanish into a wrong
number. Only "no BAT* directories at all" is allowed to come back as an
empty list — a desktop with no battery is a normal answer (see
get_packs()'s own docstring), not an error, same as swcc's own
get_batteries() treated it.

watch() (below) is this module's second, independent way of noticing a
change — push_worker.py's PushWorker consumes it instead of polling
get_packs() on a timer, found live: even a tightened 0.3s poll_interval
still felt sluggish for "plugged in the charger" specifically, because
polling has an unavoidable average latency of half its own interval.
"""

import os
import select
import time
from dataclasses import dataclass

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
    ...) currently reports itself online — the actual "is a charger
    physically connected" signal, distinct from any BAT pack's own
    `status` field (which only says whether THAT pack is presently
    accepting charge — a charger can be fully connected with every pack
    correctly declining to charge, e.g. a ThinkPad's charge_control
    threshold keeping a pack between 40-80%, see aggregate()'s own
    docstring). Found live: without this, tuicc
    had no way to show "plugged in" at all — only "actively charging" or
    a plain percentage, and the plain-percentage case reads identically
    to "genuinely unplugged, draining" even when a charger really is
    connected.

    Every entry under base_path is checked, not just one hardcoded name
    ("AC") — found live, a real ThinkPad exposes charging
    via BOTH a traditional `AC` (type=Mains) node AND two USB-C PD ports
    (type=USB, one online, one not) simultaneously; hardcoding "AC"
    alone would have missed USB-C-only charging setups entirely. Any
    entry whose own `type` isn't "Battery" and that exposes an `online`
    attribute counts as a candidate source; True if ANY of them is 1.

    None (not False) if this machine exposes NO non-battery power
    sources at all — genuinely can't tell, distinct from "checked, none
    are online" (same None-vs-[] discipline the rest of this module
    uses — see get_packs()'s own docstring).
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
    pack.

    percent: energy-weighted (sum(energy_now)/sum(energy_full)*100) when
    EVERY pack reports both energy_now and energy_full — the real
    combined charge, not skewed by packs of different physical capacity
    (found live on this very machine: BAT0 6%/22.62Wh full vs BAT1
    15%/68.7Wh full — a plain percent average reads ~10-11%, the
    energy-weighted combined charge is ~13%, a real, non-trivial
    difference). Falls back to a plain percent average when any pack is
    missing energy_* (some drivers only expose charge_*/capacity).

    status: "Charging" if ANY pack is charging (plugging in while a
    topped-off sibling reports "Not charging" should still read as "the
    machine is charging", not get overridden by the other pack) — else
    "Discharging" if any pack is — else the first pack's own status
    verbatim (covers "Full"/"Unknown" collectively, no per-pack detail
    is thrown away by the caller since it can re-derive it from `packs`
    itself if it ever needs to).

    ac_online: passed straight through from get_ac_online() — this
    function doesn't compute it itself (a separate concern, see that
    function's own docstring for why), just carries it along so the
    combined dict is the one place a caller (modules/bars.py) needs to
    look. None if the caller didn't pass one (e.g. every existing test
    in this file predating ac_online, which never cared about it).

    None (not a dict) if `packs` is empty — distinguishes "no battery in
    this machine" from "0%", same None-vs-[] discipline the rest of this
    codebase uses (see status_worker.py's own docstring).
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


def watch(stop_event, base_path: str = POWER_SUPPLY_PATH, poll_timeout: float = 1.0,
          fallback_seconds: float = 2.0):
    """Generator: primarily blocks on the KERNEL's own change notification
    for every currently-present BAT* pack, via select.poll() on each
    pack's own `uevent` sysfs attribute with POLLPRI|POLLERR — the same
    low-level mechanism upowerd/acpid use internally (the power_supply
    sysfs class documents poll() support on its attributes). Yields once
    per detected change; push_worker.py's own watch loop reacts by re-
    running get_packs()/aggregate() to get the actual fresh value — this
    generator never reads/interprets battery DATA itself, only waits for
    "something about it changed".

    NOT CURRENTLY WIRED IN (main.py reverted "battery" to a plain fast
    poll Domain) — found live, empirically, on real
    hardware (T480, NixOS): select.poll() never fired ONCE across
    several real charger unplug/replug cycles, confirmed by running
    this generator with a 30s fallback_seconds and a live logger — every
    event that arrived was exactly 30s apart (the fallback, never the
    kernel notification), and the underlying /sys data hadn't even
    changed between samples despite the physical unplug/replug having
    genuinely happened. The kernel's documented power_supply poll()
    support evidently isn't reliably wired up for this attribute on this
    exact kernel/driver combination — not something user space can
    verify ahead of time, and apparently not something to trust blindly
    on other hardware either without re-confirming live first. Left here
    (tested, does what it says) for a future revisit — e.g. a different
    machine, or checking whether a DIFFERENT power_supply attribute than
    `uevent` fires more reliably — not deleted, just not trusted as a
    primary mechanism anymore.

    ALSO yields on its own every fallback_seconds even with no kernel
    event detected — found live: a charging-start
    still went completely undetected, even after a long wait, on real
    hardware. sysfs_notify() on power_supply's `uevent` attribute is
    only actually called when the underlying driver reports a change via
    power_supply_changed() — not every ACPI battery driver necessarily
    calls that promptly (or at all) for every field this cares about
    (`status` specifically), and that's a per-driver/per-kernel detail
    with no reliable way to verify from user space ahead of time. A pure
    push design has no way to recover if the kernel simply never signals
    something changed; this fallback bounds the worst case to
    fallback_seconds instead of leaving it open-ended/silently broken —
    still push-first (instant when the kernel notification DOES fire,
    confirmed live it does for at least SOME changes on real
    hardware), with polling only as the safety net under it, not the
    primary mechanism.

    Sysfs poll() quirk, confirmed live on a real machine
    (T480, BAT0+BAT1) before relying on it: the FIRST poll() call after
    opening/registering a freshly-opened attribute fires immediately and
    unconditionally — not a real change, just how the kernel's poll
    table registration works for these files. Reading the file's
    content ("arming" it) is what makes subsequent poll() calls only
    return on an actual change; verified live — a second poll() call
    after arming genuinely blocks/times out with nothing having changed,
    a third the same. Every detected change likewise has to be re-armed
    (seek+read) before the NEXT poll() call, or that same still-
    unconsumed event immediately re-fires too.

    No packs present at generator-start time (a battery-less machine)
    returns immediately without yielding anything — a defensive floor;
    main.py's own wiring is expected to not even register this as a
    push domain in that case, this isn't the primary way that's decided.

    poll_timeout bounds how often the poll() call itself returns to
    recheck stop_event/the fallback deadline between real kernel events
    — independent of fallback_seconds, which is the actual worst-case
    staleness bound; poll_timeout just needs to be <= fallback_seconds
    for that bound to be honored (enforced below, not just assumed).
    """
    packs = get_packs(base_path)
    if not packs:
        return

    poll_timeout = min(poll_timeout, fallback_seconds)
    poller = select.poll()
    files = {}  # fd -> open file, so a fired fd can be re-armed by exact identity
    try:
        for pack in packs:
            f = open(os.path.join(base_path, pack.name, "uevent"))
            files[f.fileno()] = f
            poller.register(f.fileno(), select.POLLPRI | select.POLLERR)

        # Drain the spurious first-call fire on every fd (see docstring)
        # before entering the real wait loop.
        poller.poll(0)
        for f in files.values():
            f.seek(0)
            f.read()

        last_yield = time.monotonic()
        while not stop_event.is_set():
            events = poller.poll(poll_timeout * 1000)
            if stop_event.is_set():
                break
            now = time.monotonic()
            if events:
                for fd, _flags in events:
                    files[fd].seek(0)
                    files[fd].read()  # re-arm THIS fd before the next poll() call
                last_yield = now
                yield
            elif now - last_yield >= fallback_seconds:
                # No real kernel event, but it's been long enough that
                # this generator's own contract (never silently stale
                # past fallback_seconds) takes over — re-check anyway.
                last_yield = now
                yield
    finally:
        for f in files.values():
            f.close()
