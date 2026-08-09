"""Bars module: vertical VOL/BRI/BAT gauges — a deliberately separate
module from control.py, not sliders bolted onto it. control.py's own
docstring locks in "ONE box, uniform dense rows (dot + label) — not one
framed box per feature" as its visual rule; a continuous gauge is a
different visual language, and BAT in particular isn't something you
toggle at all (VISION.md's four-category test calls that shape
"Context": read-only decision input), so it doesn't belong in control's
row list either way. Inspired by swcc's own vertical bars (this
project's predecessor), rebuilt: at most 3 bars (VOL/BRI/BAT, BAT
omitted entirely on a battery-less machine — see _collect_bars), finer
fill resolution via eighth-block glyphs (render_utils.eighth_block_level
— same technique modules/media.py's cava visualizer already uses, fixes
swcc's own "bars move inconsistently with the real percentage"
complaint). No fixed reference ticks (25/50/75% marks) — tried live,
read as visual clutter, dropped.

Visual design, landed after a few live iterations (a per-bar framed
box, tried live, was one iteration too many — "too much" for what's
meant to be a plain, dense display): FLAT bars again, same footprint as
the very first cut (BAR_W columns, BAR_GAP between them, no border
around the fill) — but keeping the one thing that iteration was
actually for. The original bug: a partial eighth-block glyph
(▁▂▃▄▅▆▇, see render_utils.eighth_block_level) only paints ink across
the fraction of the cell it represents; the REST of that same cell used
to get drawn too (a "░" track texture, then a solid colored
background), and neither ever quite matched the empty rows around it —
found live, twice, in two different ways. The actual fix needed none of
that: if NOTHING about "empty" is ever drawn — not a texture, not a
background, just no addstr call at all, for a fully-empty row AND for
the unpainted remainder of a partial row alike — there's no separate
shade to mismatch against in the first place. Both "empty" cases render
as the exact same thing (nothing, blending straight into the terminal's
own background), trivially consistent with each other by construction.
See draw()'s own `if level == 0: continue`.

Resolution/consistency: the readout text above each bar always shows
the real, exact live percent (61%, not rounded) — the BAR's own visual
fill is a DIFFERENT, deliberately coarser number: quantized to the
nearest BAR_FILL_STEP (5%, see _quantize_percent()) before it ever
reaches eighth_block_level. Found live: without this, the same real
percentage change (say 59% -> 61%) could look like a visible jump on
one box height and nothing at all on another, purely because
eighth_block_level's own num_rows*8 resolution (8 is a hard Unicode
constant — the block glyphs only come in eighths, not a number tuicc
picked) doesn't divide evenly into 100, and that remainder shifts with
every different box height. Quantizing the FILLED value first makes
the visible step boundaries (every 5%) the same regardless of how tall
the box currently is — num_rows*8 still governs how smoothly that
already-quantized value renders within a row, not where the boundaries
fall. The readout is never quantized — it's the one place the exact
number always shows.

THIS FIRST PASS IS DISPLAY-ONLY — nav_items() returns [] on purpose
(same as clock.py), no grab/adjust interaction yet. VOL/BRI adjustment
(Enter grabs, ←→ ±5%, Enter/Esc releases) is planned as input_claim's
(R2) 4th consumer per VISION.md's own R5 section, landing as its own
follow-up pass once this pure-display cut is confirmed to look right.

---
IMPORTANT: Each module owns both how it draws itself and where its own
focusable items are — the core never guesses a module's internal
layout.
"""

import curses
from dataclasses import dataclass

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, draw_centered_lines, eighth_block_level

BAR_W = 3  # glyph columns per bar — matches the 3-char label/readout convention below
BAR_GAP = 1  # blank column between two bars

# The BAR's own fill snaps to the nearest multiple of this (see
# _quantize_percent()) — independent of box height, see this module's
# own docstring for why that's what actually fixes cross-size
# consistency, not the eighth-block resolution itself. 5, not some
# other value: matches the ±5%/keypress step VOL/BRI adjustment will
# use once input_claim interaction lands, so the bar's own visual
# language and how you actually move it agree 1:1.
BAR_FILL_STEP = 5

# Index 0 (0% filled, this row shows nothing) is unused in practice —
# draw() skips calling addstr for level 0 entirely rather than drawing
# a blank glyph (see this module's own docstring: nothing drawn at all
# is what makes "fully empty" and "a partial glyph's own unfilled
# sliver" trivially consistent with each other). Kept as a real index
# anyway so _BAR_BLOCKS[level] stays a direct 1:1 lookup matching
# eighth_block_level's own 0..8 range, same convention media.py's
# _CAVA_BLOCKS uses. Indices 1-8 = the same eighth-block glyphs cava's
# visualizer uses, via the same shared eighth_block_level().
_BAR_BLOCKS = " ▁▂▃▄▅▆▇█"

# Minimum inner content rows (box height - 2 for top/bottom border) for
# an actual vertical bar to fit: one row for the readout, at least one
# row of bar, one row for the label. Below this, draw() falls back to
# one compact text line instead of half-drawing a broken layout — same
# "compact fallback for short terminals" idea swcc's own
# draw_workspace_boxes used.
_MIN_CONTENT_ROWS_FOR_BARS = 3


@dataclass
class BarSpec:
    label: str  # "VOL"/"BRI"/"BAT" — exactly 3 chars, lines up under the bar
    percent: int | None  # 0-100 to fill, or None when there's nothing
                          # meaningful to show (poll error, or "no sinks"/
                          # "not polled yet") — the bar then renders as an
                          # empty track, never a guessed fill
    readout: str  # exactly what's shown above the bar — see _readout()
    dim: bool  # whole bar (fill + readout) rendered muted — VOL-when-
               # muted only, for now: the fill stays at its real level
               # (so you can see what it'll return to on unmute), just dimmed
    urgent: bool  # readout rendered in the theme's urgent color — a REAL
                  # poll error, not just "nothing to show yet"
    charging: bool = False  # BAT only (see _bat_spec) — the FILL itself
                             # glows the theme's "accent" color instead
                             # of the plain default, not just the
                             # readout text (which already used accent
                             # for CHG same as every normal reading) —
                             # found live, asked for: charging should
                             # visually pop, not just say "CHG" in text.
    plugged: bool = False  # BAT only, mutually exclusive with charging
                            # (see _bat_spec) — a charger IS connected
                            # (battery.get_ac_online()) but this pack
                            # isn't presently accepting charge (a
                            # ThinkPad charge_control threshold pausing
                            # mid-range, or already topped off) — found
                            # live, reported by the user: without this,
                            # "plugged in but paused" and "genuinely
                            # running on battery alone" looked
                            # IDENTICAL (both just a plain percentage),
                            # even though they're very different things
                            # to know. Readout/fill stay plain, same
                            # styling as any ordinary reading (found
                            # live, asked to drop an earlier accent-
                            # tinted version of this) — _bat_spec sets
                            # `label` to "AC" instead of "BAT" for this
                            # case, that's the only visible difference.


def _readout(percent: int | None, special: str | None = None) -> str:
    """Exactly BAR_W (3) characters, so it lines up with the bar/label
    below it — same convention swcc's own _readout used: "100%"/"0%"
    don't fit in three columns, so the two edges get their own codes.

        special   -> whatever the caller passed verbatim ("MUT"/"CHG"/"---")
        percent None -> "---" (nothing to show)
        percent >= 100 -> "MAX"   percent <= 0 -> "MIN"
        anything else -> "NN%"
    """
    if special is not None:
        return special
    if percent is None:
        return "---"
    percent = max(0, min(100, percent))
    if percent >= 100:
        return "MAX"
    if percent <= 0:
        return "MIN"
    return f"{percent:2d}%"


def _vol_spec(sinks: list | None, error: str | None) -> BarSpec:
    """sinks is ctx.status.get("audio") — a list of AudioSink, or None on
    a poll error. An empty list (audio backend genuinely reports no
    sinks) is NOT an error — same None-vs-[] discipline the rest of this
    codebase uses — so it renders as a plain unavailable dash, not urgent.
    """
    if sinks is None:
        return BarSpec("VOL", None, _readout(None), dim=False, urgent=error is not None)
    default = next((s for s in sinks if s.is_default), None)
    if default is None:
        return BarSpec("VOL", None, _readout(None), dim=False, urgent=False)
    if default.muted:
        return BarSpec("VOL", default.volume, _readout(None, special="MUT"), dim=True, urgent=False)
    return BarSpec("VOL", default.volume, _readout(default.volume), dim=False, urgent=False)


def _bri_spec(percent: int | None, error: str | None) -> BarSpec:
    if percent is None:
        return BarSpec("BRI", None, _readout(None), dim=False, urgent=error is not None)
    return BarSpec("BRI", percent, _readout(percent), dim=False, urgent=False)


def _bat_spec(data: dict | None, error: str | None) -> BarSpec | None:
    """data is ctx.status.get("battery") — battery.aggregate()'s own
    {"percent", "status", "ac_online"} dict, or None either for "no
    battery hardware on this machine" (error is also None — a normal
    answer, see battery.py's own get_packs() docstring) or "a battery IS
    there but the last poll failed" (error is set). Only the second case
    renders a bar at all — the first returns None here, and draw() omits
    the BAT bar entirely rather than showing a permanently-broken-
    looking dash on hardware that will never have one.

    Three mutually exclusive states, in priority order: Charging
    (actively adding charge — "CHG" readout, accent glow, see
    BarSpec.charging) > plugged (a charger IS connected per ac_online,
    but this pack isn't accepting charge right now — threshold-paused
    or already topped off) > plain (genuinely running on battery alone,
    no charger connected). "plugged" shows the real percent, in the
    SAME plain styling as the ordinary case — found live, asked to drop
    the earlier accent-tinted readout — and swaps the LABEL from "BAT"
    to "AC" instead, since the label is what actually reads as "there's
    a charger here" without implying an active charge.
    """
    if data is None:
        if error is not None:
            return BarSpec("BAT", None, _readout(None), dim=False, urgent=True)
        return None
    if data["status"] == "Charging":
        return BarSpec("BAT", data["percent"], _readout(None, special="CHG"),
                        dim=False, urgent=False, charging=True)
    if data.get("ac_online"):
        return BarSpec("AC", data["percent"], _readout(data["percent"]),
                        dim=False, urgent=False, plugged=True)
    return BarSpec("BAT", data["percent"], _readout(data["percent"]), dim=False, urgent=False)


def _collect_bars(ctx) -> list[BarSpec]:
    if ctx.status is None:
        return []
    sinks = ctx.status.get("audio")
    audio_error = ctx.status.get_error("audio")
    brightness_pct = ctx.status.get("brightness")
    brightness_error = ctx.status.get_error("brightness")
    battery_data = ctx.status.get("battery")
    battery_error = ctx.status.get_error("battery")

    specs = [
        _vol_spec(sinks, audio_error),
        _bri_spec(brightness_pct, brightness_error),
        _bat_spec(battery_data, battery_error),
    ]
    return [s for s in specs if s is not None]


def _quantize_percent(percent: int, step: int = BAR_FILL_STEP) -> int:
    """Rounds percent to the nearest multiple of step (clamped to
    0-100) — used for the BAR's own visual fill only, NEVER the readout
    text (see this module's own docstring for why). step<=0 just clamps,
    no rounding — defensive only, every real caller passes BAR_FILL_STEP.
    """
    percent = max(0, min(100, percent))
    if step <= 0:
        return percent
    return max(0, min(100, round(percent / step) * step))


def _compact_fallback_line(specs: list[BarSpec]) -> str:
    return "   ".join(f"{s.label} {s.readout}" for s in specs)


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color, title="Bars")

    specs = _collect_bars(ctx)
    if not specs:
        try:
            stdscr.addstr(y + 1, x + 2, "(no bars available)", theme.get("text", 0) | curses.A_DIM)
        except curses.error:
            pass
        return

    content_rows = h - 2
    if content_rows < 1:
        return

    if content_rows < _MIN_CONTENT_ROWS_FOR_BARS:
        draw_centered_lines(stdscr, box, [(_compact_fallback_line(specs), theme.get("text", 0))])
        return

    inner_w = max(w - 2, 0)
    total_w = len(specs) * BAR_W + (len(specs) - 1) * BAR_GAP
    start_x = x + 1 + max((inner_w - total_w) // 2, 0)

    label_row = y + h - 2
    bar_bottom = label_row - 1
    bar_rows = content_rows - 2
    readout_row = bar_bottom - bar_rows

    label_color = theme.get("text", 0) | curses.A_DIM

    for i, spec in enumerate(specs):
        bx = start_x + i * (BAR_W + BAR_GAP)

        if spec.urgent:
            readout_color = theme.get("urgent", 0) | curses.A_BOLD
            fill_color = theme.get("text", 0)
        elif spec.dim:
            readout_color = theme.get("text", 0) | curses.A_DIM
            fill_color = theme.get("text", 0) | curses.A_DIM
        elif spec.charging:
            readout_color = theme.get("accent", 0) | curses.A_BOLD
            fill_color = theme.get("accent", 0) | curses.A_BOLD
        else:
            # Also covers "plugged" (spec.plugged) — same plain styling
            # as any ordinary reading, found live, asked to drop the
            # earlier accent-tinted readout here. "plugged" shows up
            # through the LABEL ("AC" instead of "BAT", see _bat_spec's
            # own docstring), not through color.
            readout_color = theme.get("accent", 0) | curses.A_BOLD
            fill_color = theme.get("text", 0) | curses.A_BOLD

        try:
            stdscr.addstr(readout_row, bx, spec.readout.rjust(BAR_W), readout_color)
        except curses.error:
            pass

        raw_value = spec.percent if spec.percent is not None else 0
        value = _quantize_percent(raw_value)  # bar's own fill only — spec.readout above stays exact
        for row_offset in range(bar_rows):
            row = bar_bottom - row_offset  # row_offset=0 is the BOTTOMMOST row (0% start)
            row_idx = bar_rows - 1 - row_offset  # eighth_block_level's own row_idx: 0=top

            level = eighth_block_level(value, 100, row_idx, bar_rows)
            if level == 0:
                continue  # nothing drawn at all — blends straight into the terminal background,
                          # same as a partial glyph's own unpainted remainder below (never drawn either)
            glyph = _BAR_BLOCKS[level]

            try:
                stdscr.addstr(row, bx, glyph * BAR_W, fill_color)
            except curses.error:
                pass

        try:
            stdscr.addstr(label_row, bx, spec.label.ljust(BAR_W), label_color)
        except curses.error:
            pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    """Empty on purpose — this first pass is display-only, see this
    module's own docstring. Grab/adjust interaction (a real NavItem per
    bar, TARGET_KIND + input_claim handling) is a follow-up pass.
    """
    return []
