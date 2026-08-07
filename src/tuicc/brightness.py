"""Screen brightness via brightnessctl — VISION.md's R5. A single,
plain module (not a registry-backed backend package like audio/
connectivity) since there's exactly one reasonable tool for this on a
real machine, not several to choose between the way wifi/bluetooth/
audio genuinely have multiple viable implementations.

`brightnessctl -m` with no `-d`/`-c` picks its own default device —
verified live this reliably lands on the real screen backlight
(class=backlight), not one of the several `class=leds` devices
(capslock indicator, mic-mute LED, ThinkLight, ...) also listed by
`brightnessctl -m -l` on this machine. Not filtering by class
explicitly is a deliberate choice matching brightnessctl's own
default-device behavior, not an oversight — revisit only if a real
machine turns up where that default picks the wrong device.
"""

import subprocess


def parse_get_output(text: str) -> int | None:
    """`brightnessctl -m`'s one line (`device,class,current,percent,
    max`, e.g. "intel_backlight,backlight,1515,100%,1515") -> the
    percent field as a plain int. None if the line doesn't look like
    that shape at all (empty output, or brightnessctl printed
    something unexpected) — the caller (get_percent()) turns that into
    a real, raised error instead of guessing a fallback value.
    """
    stripped = text.strip()
    if not stripped:
        return None
    line = stripped.splitlines()[0]
    parts = line.split(",")
    if len(parts) < 4:
        return None
    percent_field = parts[3]
    if not percent_field.endswith("%"):
        return None
    try:
        return int(percent_field.rstrip("%"))
    except ValueError:
        return None


def get_percent() -> int:
    """No internal try/except — same reasoning as audio/connectivity's
    own backends (see their own R3-follow-up docstrings): a
    brightnessctl failure must propagate to
    status_worker.StatusWorker's poll wrapper, not vanish into a
    silent fallback value. Raises RuntimeError if brightnessctl ran
    but its output didn't parse as expected — distinct from a
    subprocess-level failure (missing binary, timeout), which already
    propagates on its own via subprocess.run itself.
    """
    result = subprocess.run(["brightnessctl", "-m"], capture_output=True, text=True, timeout=5)
    percent = parse_get_output(result.stdout)
    if percent is None:
        raise RuntimeError(f"brightnessctl -m produced unexpected output: {result.stdout!r}")
    return percent


def set_percent(percent: int) -> None:
    """Clamped to 0-100 here rather than trusting the caller (a slider
    UI overshoot on a fast keypress is exactly the kind of "off by one
    from the edge" bug worth guarding against at the boundary that
    actually owns the valid range, not upstream in modules/control.py).
    """
    percent = max(0, min(100, percent))
    subprocess.run(["brightnessctl", "set", f"{percent}%"], capture_output=True, text=True, timeout=5)
