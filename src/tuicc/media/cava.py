"""CavaReader: streams a continuous audio-visualizer feed from the
`cava` CLI (https://github.com/karlstav/cava) — genuinely different
shape from every other backend in this codebase. Everything else here
is "ask once, get an answer" (a Domain's poll()); this is "spawn once,
keep reading forever" — cava streams one line of bar-height data per
frame at its own internal framerate, indefinitely, until killed. Closer
to StatusWorker's own thread-per-worker pattern than to a Domain's
poll() model, as VISION.md's R5 backlog entry called out — this is
that follow-on, not wired through StatusWorker/Domain at all.
main.py owns a single CavaReader instance directly, starting/stopping
it based on whether anything is actually playing (see main.py's own
wiring), not polling it on a timer.

cava can't change its own bar count without a restart — there's no
live "resize" over a raw/headless stream the way its own ncurses UI
resizes with the terminal it's attached to — so `bars` is fixed at
construction time, and modules/media.py just clips/pads at render time
if the box width doesn't match exactly.
"""

import subprocess
import threading
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "tuicc" / "cava.conf"

# cava's own ascii_max_range (not left at its 1000 default) — a fixed
# value independent of how many terminal rows actually display it.
# Found live: the visualizer moved from 2 fixed rows below Output to a
# variable-height strip beside it (1 row per audio sink currently
# connected, however many that is), so the row count isn't known until
# render time — modules/media.py's own _cava_row_level() scales this
# fixed 0..ASCII_MAX_RANGE range down to whatever 0..(num_rows*8)
# resolution is actually available that frame. 64 gives room for up to
# 8 rows of 8-level resolution each — more sinks than any realistic
# setup, so this is headroom, not a tight fit.
ASCII_MAX_RANGE = 64

# method = pipewire, not left to cava's own auto-detection order — every
# current sway/i3 desktop this project targets runs PipeWire (same
# reasoning audio/wpctl.py's own primary-backend choice gives).
# mono/average, not stereo: a deliberate choice made before building this,
# see VISION.md's R5 backlog entry — 2 rows are spent on finer VERTICAL
# resolution per bar, not on a left/right channel split.
_CONFIG_TEMPLATE = """\
[general]
bars = {bars}
framerate = 30

[input]
method = pipewire
source = auto

[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = {levels}
channels = mono
mono_option = average
"""


def parse_cava_line(line: str) -> list[int]:
    """Pure logic half of reading a frame — cava's raw/ascii output is
    one semicolon-delimited line of bar-height integers per frame,
    confirmed against a real cava run. Split out from _read_loop so
    it's testable without a real subprocess, same split mpris.py's own
    filter_mpris_names/parse_metadata make for the D-Bus side.
    """
    return [int(v) for v in line.strip().split(";") if v]


class CavaReader:
    def __init__(self, bars: int = 24):
        self.bars = bars
        self._process = None
        self._thread = None
        self._lock = threading.Lock()
        self._frame = None  # list[int] | None — latest parsed bar heights, 0..ASCII_MAX_RANGE each
        self._error = None  # str | None — a REAL failure only, see get_error()'s own docstring
        # Set once, permanently, the first time start() finds cava
        # missing — NOT the same thing as _error (see start()'s own
        # comment on the FileNotFoundError branch for why a missing
        # binary specifically does NOT count as an "error" here).
        # Remembered so main.py's every-frame-something's-playing
        # start() calls don't keep re-attempting a spawn that's going
        # to fail identically every single time, for as long as
        # anything plays — cheap insurance, not a correctness issue.
        self._binary_missing = False

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self):
        """No-op if already running, or if a previous attempt already
        found the binary missing — main.py calls this every frame
        something's playing rather than tracking "did I already try
        this" itself, same convenience stop() gives below.
        """
        if self.is_running() or self._binary_missing:
            return
        self._write_config()
        with self._lock:
            self._error = None
            self._frame = None
        try:
            self._process = subprocess.Popen(
                ["cava", "-p", str(CONFIG_PATH)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
        except FileNotFoundError:
            # cava genuinely not installed — deliberately NOT the same
            # treatment as a real failure elsewhere in this codebase.
            # Explicit user decision: this is an optional cosmetic extra
            # with zero functional impact when absent (unlike wifi/
            # bluetooth/audio actually not working), so it doesn't
            # belong in get_error()/rendered as a warning — the media
            # module just stays in its normal idle/flatline state
            # forever, the same as if nothing were ever playing.
            self._binary_missing = True
            self._process = None
            return
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """No-op if not running. terminate() first (lets cava release
        its PipeWire capture cleanly), kill() only if it doesn't exit
        promptly — same tiered-shutdown idea, not because cava is known
        to hang, just cheap insurance against a background thread never
        rejoining and leaking a real audio-capture process.
        """
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._process = None
        self._thread = None
        with self._lock:
            self._frame = None

    def get_frame(self):
        """The latest parsed line of bar heights, or None if cava isn't
        running or hasn't produced a line yet (right after start()).
        """
        with self._lock:
            return self._frame

    def get_error(self):
        """Set if cava crashed or exited unexpectedly WHILE running —
        surfaced by modules/media.py as a real error row, same
        no-silent-failure treatment as every other backend, not a blank
        or frozen visualizer with no explanation. Deliberately NOT set
        just because the binary is missing (see start()'s own comment)
        — that specific case has zero functional impact and doesn't
        warrant interrupting anyone, unlike an actual runtime failure.
        """
        with self._lock:
            return self._error

    def _write_config(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(_CONFIG_TEMPLATE.format(bars=self.bars, levels=ASCII_MAX_RANGE))

    def _read_loop(self):
        """Runs in its own thread for as long as the process lives.
        Exception boundary here matches status_worker.py's own _run()
        (see its module docstring) — a background thread has no
        synchronous caller to propagate an exception to, so "no internal
        try/except" doesn't apply the way it does everywhere else in
        this codebase; the outer try/except IS the boundary where a real
        failure must be caught and turned into recorded state, or it
        vanishes into a dead thread with get_error() never finding out.
        """
        process = self._process
        try:
            for line in process.stdout:
                if not line.strip():
                    continue
                heights = parse_cava_line(line)
                with self._lock:
                    self._frame = heights
        except Exception as e:
            with self._lock:
                self._error = str(e) or type(e).__name__
            return
        # stdout closed without an exception — either stop() did it
        # (self._process was already reset to None by the time we get
        # here) or cava exited on its own (a real, unexpected failure).
        if self._process is process:
            stderr_output = process.stderr.read() if process.stderr else ""
            with self._lock:
                self._error = stderr_output.strip() or "cava exited unexpectedly"
