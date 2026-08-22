"""Per-window CPU/RAM sampling for VISION.md's R6 system monitor — full
process-SUBTREE aggregated, not per-leaf: a window's own top-level pid
is very often just a thin wrapper (a shell launching a browser, an
Electron main process forking renderer children, ...), so attributing
CPU/RAM to only that one pid would silently undercount almost every
real app. Every descendant of a window's pid, found via one full
/proc/*/stat scan per poll (not N separate per-window walks), gets
summed into that window's own totals.

Two halves, deliberately separated by which thread each is allowed to
touch:

- Everything in this file is pure system/`/proc` reading — no Provider,
  no curses, safe to run on StatusWorker's background poll thread, same
  as every other Domain's `poll`.
- `PidFeed` is the one piece of shared state that crosses the thread
  boundary: main.py (main thread) flattens `WMState.regions` into
  `WindowInfo` objects right after its own `provider.get_state()` call
  and publishes them via `PidFeed.set()`; the "windows" Domain's
  `poll()` (background thread) only ever reads them back via
  `PidFeed.get()`. `Provider` itself stays main-thread-only this way —
  no Domain closes over `provider` directly, matching every other
  Domain in this codebase (wifi/bluetooth/audio/etc. all read their own
  independent system/D-Bus sources, never the WM connection).
"""

import os
import threading
import time
from dataclasses import dataclass, field

CLK_TCK = os.sysconf("SC_CLK_TCK")  # ticks/second the kernel reports utime/stime in — almost always 100
PROC_PATH = "/proc"


@dataclass
class WindowInfo:
    """One window's identity, as seen by the WM — the shape PidFeed
    carries across the thread boundary. `window_id` is the WM's own
    con_id (Window.id in model.py) — what Provider.close_window() takes.
    `pid` is None whenever the provider can't supply one (i3's IPC tree
    doesn't — see providers/base.py's resolve_pid() docstring); a window
    with no pid still shows up as a row, just with unknown CPU/RAM
    (ProcMonSampler.poll() below), same "degraded, not broken" pattern
    the rest of tuicc uses for optional data.
    """
    window_id: str
    app_id: str
    title: str
    pid: int | None


class PidFeed:
    """Thread-safe container for the current frame's window list — see
    this module's own docstring for why this exists instead of a
    background Domain reading `provider`/`ctx.state` directly. Same
    "expose the live object itself, not a snapshot the caller has to
    remember to refresh" convention `RenderContext.cava` already uses
    for `CavaReader` (context.py) — main.py holds one instance, calls
    `.set()` once per frame, `modules/sysmon.py` never touches it
    directly (only `ctx.status.get("windows")`, same as every other
    domain).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._windows: list[WindowInfo] = []

    def set(self, windows: list[WindowInfo]) -> None:
        with self._lock:
            self._windows = windows

    def get(self) -> list[WindowInfo]:
        with self._lock:
            return list(self._windows)


def flatten_windows(state) -> list[WindowInfo]:
    """WMState -> one WindowInfo per real window, across every region
    (workspace) — pure, main-thread-only (reads the same `state` object
    main.py's own `provider.get_state()` call already produced this
    frame, never touches `provider` itself). tuicc's own window is
    already excluded upstream by `mark_self()`/`get_state()` (see
    providers/base.py's own docstring), so nothing extra to filter here.
    """
    windows = []
    for region in state.regions:
        for window in region.windows:
            windows.append(WindowInfo(
                window_id=window.id, app_id=window.app_id,
                title=window.title, pid=window.pid,
            ))
    return windows


@dataclass
class _ProcSample:
    pid: int
    ppid: int
    utime: int  # ticks, this process's own (not children's) user-mode time
    stime: int  # ticks, this process's own (not children's) kernel-mode time


def _read_stat_line(pid: int, proc_path: str = PROC_PATH) -> _ProcSample | None:
    """One process's own /proc/<pid>/stat fields — None if the process
    exited between the /proc listdir and this read (a normal race on a
    live system, not a real I/O failure; see scan_all_processes()'s own
    docstring for why that distinction matters here).

    comm (field 2) is parenthesized and may itself contain spaces/
    parens ("(some (weird) name)") — found documented in proc(5); the
    only reliable split is right after the LAST ')', not a naive
    space-split of the whole line.
    """
    try:
        with open(f"{proc_path}/{pid}/stat") as f:
            line = f.read()
    except (FileNotFoundError, ProcessLookupError):
        return None
    rparen = line.rfind(")")
    if rparen == -1:
        return None
    fields = line[rparen + 2:].split()
    # fields[0] here is stat's field 3 (state); field 4 (ppid) is
    # fields[1], field 14/15 (utime/stime) are fields[11]/fields[12].
    try:
        ppid = int(fields[1])
        utime = int(fields[11])
        stime = int(fields[12])
    except (IndexError, ValueError):
        return None
    return _ProcSample(pid=pid, ppid=ppid, utime=utime, stime=stime)


def scan_all_processes(proc_path: str = PROC_PATH) -> dict[int, _ProcSample]:
    """Every currently-running process's own /proc/<pid>/stat, in ONE
    pass — the ppid map every window's subtree walk (subtree_pids
    below) is built from, so aggregating N windows costs one scan, not
    N. Processes that exit mid-scan are just skipped (see
    _read_stat_line's own docstring) — a live system's process list
    changing between listdir() and each individual read is expected,
    not a fault to raise on. `proc_path` defaults to real /proc but is
    a parameter (not hardcoded) so tests can point this at a fake tree
    under tmp_path instead of a live system — same pattern battery.py's
    own base_path parameter already established.
    """
    samples = {}
    try:
        entries = os.listdir(proc_path)
    except OSError:
        return {}
    for entry in entries:
        if not entry.isdigit():
            continue
        sample = _read_stat_line(int(entry), proc_path)
        if sample is not None:
            samples[sample.pid] = sample
    return samples


def build_children_map(samples: dict[int, _ProcSample]) -> dict[int, list[int]]:
    """ppid -> [child pids], from one scan_all_processes() snapshot.
    Public (not module-private) — pending_moves.py's own fork/exec
    pid-mismatch tracking (CLAUDE/NOTES/known-limitations.md
    #fork-exec-pid-mismatch) reuses this and subtree_pids below
    directly, the same subtree-walk this module built for R6's own
    per-window process aggregation.
    """
    children: dict[int, list[int]] = {}
    for pid, sample in samples.items():
        children.setdefault(sample.ppid, []).append(pid)
    return children


def subtree_pids(root_pid: int, children_map: dict[int, list[int]]) -> list[int]:
    """root_pid + every descendant, via children_map (see
    build_children_map) — a real process tree can't cycle, but the
    `seen` guard costs nothing and makes this safe against a
    theoretically malformed map regardless.
    """
    result = []
    seen = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        result.append(pid)
        stack.extend(children_map.get(pid, []))
    return result


def _read_rss_kb(pid: int, proc_path: str = PROC_PATH) -> int | None:
    """This one process's own resident memory, in kB, from
    /proc/<pid>/status's VmRSS line — None if the process is gone
    (same race as _read_stat_line) or the field is genuinely absent
    (a handful of kernel-thread-like edge cases): distinct from a
    real 0, so callers summing across a subtree skip None entries
    rather than let one missing process silently zero out the total.
    """
    try:
        with open(f"{proc_path}/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (FileNotFoundError, ProcessLookupError):
        return None
    return None  # no VmRSS line for this pid — treat as unknown, not 0


@dataclass
class WindowStat:
    """One window's resolved CPU/RAM — what the "windows" Domain's
    poll() actually returns, one per WindowInfo passed to
    ProcMonSampler.poll().
    """
    window_id: str
    app_id: str
    title: str
    pid: int | None
    # None until a SECOND poll has happened (CPU% is inherently a
    # delta between two timestamped samples — see ProcMonSampler's own
    # docstring) — same None-vs-0 discipline the rest of this codebase
    # uses; a fresh window's CPU% is "not yet known", not "0%".
    cpu_percent: float | None
    rss_kb: int | None


@dataclass
class ProcMonSampler:
    """Stateful CPU%-delta sampler — same reasoning battery.py's own
    aggregate()-from-snapshot needs a caller to hold prior state for
    (there, energy_now/energy_full deltas aren't even needed since the
    kernel already reports a percent; here, the kernel only reports
    CUMULATIVE ticks, so this class is what turns two timestamped
    cumulative snapshots into a percent). One instance lives for the
    "windows" Domain's whole lifetime (constructed once in main.py's
    Domain registration, not per-poll) so consecutive polls actually
    see each other's prior state.
    """
    _prev_samples: dict[int, _ProcSample] = field(default_factory=dict)
    _prev_time: float | None = None

    def poll(self, windows: list[WindowInfo], proc_path: str = PROC_PATH) -> list[WindowStat]:
        samples = scan_all_processes(proc_path)
        children_map = build_children_map(samples)
        now = time.monotonic()
        elapsed = (now - self._prev_time) if self._prev_time is not None else None

        results = []
        for w in windows:
            if w.pid is None:
                results.append(WindowStat(w.window_id, w.app_id, w.title, None, None, None))
                continue

            pids = subtree_pids(w.pid, children_map)
            total_ticks = sum(
                samples[p].utime + samples[p].stime for p in pids if p in samples
            )

            cpu_percent = None
            if elapsed and elapsed > 0:
                prev_ticks = sum(
                    self._prev_samples[p].utime + self._prev_samples[p].stime
                    for p in pids if p in self._prev_samples
                )
                delta_ticks = total_ticks - prev_ticks
                cpu_percent = max(0.0, (delta_ticks / CLK_TCK) / elapsed * 100)

            rss_values = [_read_rss_kb(p, proc_path) for p in pids]
            rss_values = [v for v in rss_values if v is not None]
            rss_kb = sum(rss_values) if rss_values else None

            results.append(WindowStat(w.window_id, w.app_id, w.title, w.pid, cpu_percent, rss_kb))

        self._prev_samples = samples
        self._prev_time = now
        return results
