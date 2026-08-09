"""System-wide stats for VISION.md's R6 system monitor's middle
"compact stats" section — CPU%, RAM%, disk usage, load average, a CPU
throttle flag, and swap I/O rate. Pure /proc + /sys + shutil reads, no
curses, no Provider — safe on StatusWorker's background poll thread
like every other Domain. Independent of procmon.py's PER-WINDOW
aggregation — a separate data source, registered under its own
"sysinfo" Domain name, sharing nothing with "windows" except both
ultimately reading /proc.

Three of these values are stateful deltas, not a single read — the
kernel only exposes CUMULATIVE counters for CPU time, swapped pages,
and throttle events, same reasoning ProcMonSampler needs a prior
snapshot to turn cumulative /proc/<pid>/stat ticks into a percent (see
procmon.py's own docstring). SysInfoSampler is this module's equivalent
stateful class — one instance registered for the "sysinfo" Domain's
whole lifetime, not reconstructed per-poll, so consecutive polls
actually see each other's prior state.
"""

import os
import shutil
import time
from dataclasses import dataclass

PROC_PATH = "/proc"
CPU_THROTTLE_PATH = "/sys/devices/system/cpu"
DISK_PATH = "/"


def parse_cpu_line(line: str) -> tuple[int, int] | None:
    """(total, idle) jiffies from /proc/stat's own "cpu  ..." summary
    line — the first 8 fields (user/nice/system/idle/iowait/irq/
    softirq/steal), same subset `top`/most monitoring tools use; guest/
    guest_nice (fields 9-10, present on modern kernels) are already
    counted inside user/nice per the kernel's own /proc/stat
    documentation, so including them again would double-count. Older
    kernels exposing fewer than 8 fields (pre-steal, ~2.6.11) pad with
    0 rather than fail — a real value from an old kernel is still
    better than treating the whole line as unreadable.
    """
    parts = line.split()
    if not parts or parts[0] != "cpu":
        return None
    values = [int(x) for x in parts[1:]]
    while len(values) < 8:
        values.append(0)
    idle = values[3] + values[4]  # idle + iowait
    total = sum(values[:8])
    return total, idle


def read_cpu_times(proc_path: str = PROC_PATH) -> tuple[int, int] | None:
    try:
        with open(f"{proc_path}/stat") as f:
            first_line = f.readline()
    except OSError:
        return None
    return parse_cpu_line(first_line)


def parse_meminfo(text: str) -> dict[str, int]:
    """{"MemTotal": 16345678, ...} — values in kB, the unit every
    /proc/meminfo line already uses; the " kB" suffix itself is
    dropped, not converted, since every field shares the same unit.
    """
    result = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        pieces = rest.strip().split()
        if not pieces:
            continue
        try:
            result[key] = int(pieces[0])
        except ValueError:
            continue
    return result


def get_ram_info(proc_path: str = PROC_PATH) -> dict | None:
    """{"total_kb", "available_kb", "used_kb", "percent"} — same
    MemAvailable-based calculation get_ram_percent() (below) uses, also
    exposing the underlying kB counts so a caller (sysmon.py's own
    stats grid) can show real used/available amounts in GiB, not just
    a bare percentage — found live, asked for ("RAM 70% je fajn ale
    lepší by bylo U/A v GiB"). None under the same conditions
    get_ram_percent() already returns None for (can't read meminfo, or
    no MemTotal).
    """
    try:
        with open(f"{proc_path}/meminfo") as f:
            text = f.read()
    except OSError:
        return None
    mem = parse_meminfo(text)
    total = mem.get("MemTotal")
    if not total:
        return None
    available = mem.get("MemAvailable")
    if available is None:
        # MemAvailable was only added in Linux 3.14 — a genuinely
        # ancient kernel lacking it falls back to the older, rougher
        # free+buffers+cached approximation rather than reporting
        # nothing at all.
        available = mem.get("MemFree", 0) + mem.get("Buffers", 0) + mem.get("Cached", 0)
    available = max(0, min(available, total))
    used = total - available
    percent = max(0.0, min(100.0, 100.0 * used / total))
    return {"total_kb": total, "available_kb": available, "used_kb": used, "percent": percent}


def get_ram_percent(proc_path: str = PROC_PATH) -> float | None:
    """None if /proc/meminfo can't be read at all, or doesn't expose
    MemTotal — genuinely can't tell, distinct from a real 0%. Thin
    wrapper over get_ram_info() (above) — kept as its own function
    since it predates get_ram_info and nothing else needs the fuller
    dict.
    """
    info = get_ram_info(proc_path)
    return info["percent"] if info is not None else None


def get_load_average(proc_path: str = PROC_PATH) -> tuple[float, float, float] | None:
    """Read from /proc/loadavg directly rather than os.getloadavg() —
    same reasoning every other read in this module takes a `proc_path`
    parameter: fixture-testable against a fake tree, no monkeypatching
    of a C-level os function needed.
    """
    try:
        with open(f"{proc_path}/loadavg") as f:
            text = f.read()
    except OSError:
        return None
    parts = text.split()
    if len(parts) < 3:
        return None
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None


def get_disk_usage(path: str = DISK_PATH) -> dict | None:
    """{"total", "used", "free", "percent"} (bytes, bytes, bytes,
    0..100) for the filesystem mounted at `path` — None if the path
    doesn't exist or isn't readable (e.g. a container/chroot with an
    unusual root). shutil.disk_usage wraps statvfs() directly; no
    /proc equivalent exists, so this is the one stat in this module not
    driven by a `proc_path`-style injectable base — tests monkeypatch
    shutil.disk_usage itself instead (same idea, different mechanism).
    """
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    percent = (100.0 * usage.used / usage.total) if usage.total else None
    return {"total": usage.total, "used": usage.used, "free": usage.free, "percent": percent}


def parse_vmstat(text: str) -> dict[str, int]:
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        key, value = parts
        try:
            result[key] = int(value)
        except ValueError:
            continue
    return result


def read_swap_pages(proc_path: str = PROC_PATH) -> tuple[int, int] | None:
    """(pswpin, pswpout) — cumulative page counts swapped in/out since
    boot, from /proc/vmstat. None if the file can't be read, or (some
    minimal/embedded kernel configs) doesn't expose these two fields at
    all — distinct from "read fine, currently 0 lifetime swaps".
    """
    try:
        with open(f"{proc_path}/vmstat") as f:
            text = f.read()
    except OSError:
        return None
    stats = parse_vmstat(text)
    if "pswpin" not in stats or "pswpout" not in stats:
        return None
    return stats["pswpin"], stats["pswpout"]


def read_throttle_count(sys_path: str = CPU_THROTTLE_PATH) -> int | None:
    """Sum of every CPU core's own cumulative thermal-throttle event
    count (/sys/devices/system/cpu/cpuN/thermal_throttle/
    core_throttle_count — Intel-specific sysfs, not every CPU
    family/driver exposes it). None if sys_path itself doesn't exist,
    or no core under it exposes a throttle_count file at all — a
    machine that genuinely can't report this, distinct from "checked,
    it's currently 0 events".
    """
    if not os.path.isdir(sys_path):
        return None
    total = None
    for entry in sorted(os.listdir(sys_path)):
        if not (entry.startswith("cpu") and entry[3:].isdigit()):
            continue
        count_path = os.path.join(sys_path, entry, "thermal_throttle", "core_throttle_count")
        try:
            with open(count_path) as f:
                value = int(f.read().strip())
        except (OSError, ValueError):
            continue
        total = (total or 0) + value
    return total


@dataclass
class SysInfoSampler:
    """Stateful wrapper turning the cumulative counters above into
    per-interval rates/deltas — see this module's own docstring for why
    a single poll() call alone can't produce cpu_percent/swap rates/
    throttled_recently. poll() returns a plain dict (this Domain's own
    `poll` callable, registered with StatusWorker) — a single dict, not
    a list, same precedented shape battery.aggregate()/
    brightness.get_percent already use for a non-list Domain.
    """
    _prev_cpu: tuple[int, int] | None = None
    _prev_swap: tuple[int, int] | None = None
    _prev_throttle: int | None = None
    _prev_time: float | None = None

    def poll(self, proc_path: str = PROC_PATH, sys_path: str = CPU_THROTTLE_PATH,
              disk_path: str = DISK_PATH) -> dict:
        now = time.monotonic()
        elapsed = (now - self._prev_time) if self._prev_time is not None else None

        cpu_times = read_cpu_times(proc_path)
        cpu_percent = None
        if cpu_times is not None and self._prev_cpu is not None and elapsed and elapsed > 0:
            total, idle = cpu_times
            prev_total, prev_idle = self._prev_cpu
            delta_total = total - prev_total
            delta_idle = idle - prev_idle
            if delta_total > 0:
                cpu_percent = max(0.0, min(100.0, 100.0 * (1 - delta_idle / delta_total)))

        swap_pages = read_swap_pages(proc_path)
        swap_in_kb_s = swap_out_kb_s = None
        if swap_pages is not None and self._prev_swap is not None and elapsed and elapsed > 0:
            page_kb = os.sysconf("SC_PAGE_SIZE") / 1024
            pswpin, pswpout = swap_pages
            prev_in, prev_out = self._prev_swap
            swap_in_kb_s = max(0.0, (pswpin - prev_in) * page_kb / elapsed)
            swap_out_kb_s = max(0.0, (pswpout - prev_out) * page_kb / elapsed)

        throttle_count = read_throttle_count(sys_path)
        throttled_recently = None
        if throttle_count is not None and self._prev_throttle is not None:
            throttled_recently = throttle_count > self._prev_throttle

        ram_info = get_ram_info(proc_path)
        result = {
            "cpu_percent": cpu_percent,
            "ram_percent": ram_info["percent"] if ram_info is not None else None,
            "ram": ram_info,
            "disk": get_disk_usage(disk_path),
            "load_average": get_load_average(proc_path),
            "throttled_recently": throttled_recently,
            "swap_in_kb_s": swap_in_kb_s,
            "swap_out_kb_s": swap_out_kb_s,
        }

        self._prev_cpu = cpu_times
        self._prev_swap = swap_pages
        self._prev_throttle = throttle_count
        self._prev_time = now
        return result
