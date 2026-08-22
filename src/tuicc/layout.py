"""
Layout model: where each module sits on screen, as ratios of the whole terminal.

    preset (built-in) ──┐
                         ├─> Layout [ratios, defined here] ─┐
    user config delta ──┘                                   │
                                                              v
                                                layout_engine + terminal size
                                                              │
                                                              v
                                                     absolute boxes (cells)
                                                              │
                                                              v
                                                     modules draw themselves

This file only defines the *shape* of a layout. Combining presets with
user overrides, and converting all of that into actual terminal
rows/columns, happens in layout_engine.py.

Every box is a plain, independent x/y ratio (0.0-1.0 of the terminal's
width/height) — nothing here coordinates with anything else. Resizing
or repositioning one box never moves or resizes another; what you
configure is exactly what renders. If a box's ratio looks wrong on a
very different terminal size than the one you set it up on, that's
expected — interactive resize mode (main.py) is the fix-it-when-you-
see-it mechanism, not an automatic guarantee.

Width and height each have the same one exception, independently: a
box can use `w`/`h` (ratio, as above) OR `fw`/`fh` (fixed columns/rows
— an absolute terminal-cell count, never scaled). Found live (height
first — width mirrors it exactly, added once the same real need showed
up on that axis too): some modules (control's toggle list,
connectivity/media/sysmon's fixed-row tables, sessions/power_menu/
launcher/rwb's own compact strips) need a SPECIFIC row count to look
right — fewer rows and real content gets clipped/unusable, more rows
and it's just wasted empty space, so scaling them proportionally with
the terminal is actively wrong, not just imprecise the way it's fine
for a genuinely elastic box (sidebar, preview) to be; bars is the
matching width case — meant to grow/shrink its HEIGHT freely but wants
a specific, unchanging COLUMN count to look right regardless of
terminal size. Exactly one of w/fw, and independently exactly one of
h/fh, must be set per box — config.py's build_layout_from_preset()
raises loudly on the box's name if either pair is both or neither
given, same no-silent-fallback discipline as everywhere else in this
codebase. A box can freely mix — fh height with ratio width, fw width
with ratio height, or fw+fh on both axes at once — the two axes are
handled completely independently throughout this whole file and
layout_engine.py, never coupled.

A second, related exception, for boxes that use `h`/`w` (a "flexible"
box — genuinely elastic, meant to grow/shrink with the terminal): one
can still end up intruding into a neighboring box on a terminal short
enough to force it — most commonly a `fh`/`fw` neighbor, which never
shrinks by definition, but any other box's rect works the same way.
layout_engine.py's compute_boxes() detects this (2D rect overlap,
restricted to boxes that share space on the OTHER axis — a box in a
different column can't visually collide vertically regardless of its
own y range, and likewise a box in a different row can't collide
horizontally regardless of its own x range) and shrinks the flexible
box just enough to stop intruding: cut from the top/left if the other
box sits above/left, from the bottom/right if it sits below/right, both
if sandwiched between two. If nothing is left (fully squeezed out), the
box gets 0 size on that axis and doesn't draw at all that frame, rather
than being silently clipped or drawn overlapping — see that function's
own docstring for the exact algorithm. Still no coordination in the
sense that matters (a box's OWN x/y/w/h/fw/fh in the preset never
changes — this is purely a render-time consequence of the current
terminal size, same category of exception as fh/fw's own edge-hold
above, just applied to the other kind of box).

A third exception, for two `fh` (or two `fw`) boxes stacked/lined-up
directly against each other (e.g. control above power_menu; bars is the
only fw box today and has no fw sibling yet, but the mechanism is
general) — each one's edge-hold only knows about the terminal's own
edge, not about a SIBLING fixed box also holding that same edge, so two
independently edge-held fixed boxes can draw straight into each other
on a short enough terminal. Also resolved in compute_boxes() (its own
docstring has the exact chaining algorithm) — positional only, never
resizes either box, since fh/fw can't shrink by definition either way.
Height and width are chained completely independently (an fh chain
never looks at fw boxes or vice versa) — a box that's fh on one axis
and fw on the other participates in both chains separately.

`fh_auto` (height only — no `fw_auto` yet, nothing's asked for it):
opts a box that's already in `fh` mode into having that number
RECOMPUTED from its own module's real content every time the layout is
(re)built — see render.py's `AUTO_FH_PROVIDERS`/`apply_auto_fh()` for
the actual computation, and each of control.py/connectivity.py/
media.py/sessions.py/rwb.py's own `required_fh()` for what each one
counts. Only meaningful alongside `fh` (raises if set on a `h`-ratio
box — nothing to recompute). Found live: `fh` was originally just a
number Rafi hand-tuned via F2 to fit his OWN config's content exactly
(how many toggles he has enabled, how many wifi/bt slots he shows) —
a fresh install with a different toggle count would get a box that's
too big or too small for ITS content, undermining the whole "nobody
except me gets a working layout out of the box" goal fh/fw were built
for in the first place. `fh_auto` fixes that by construction: the
value tracks content, not a one-time guess.

**"Auto until you touch it," not "always auto":** the moment
resize_mode.py's resize_step() actually resizes a box's height (not
just enters editing — the user has to press a grow/shrink key on the
h dimension), `fh_auto` flips to `False` and freezes whatever fh value
the box had at that instant as a plain manual number from then on,
exactly like a box that never had `fh_auto` at all. This was a real,
explicit decision, not an obvious default — the alternative (auto
always wins, every launch) would silently undo a deliberate F2 resize
the next time control_toggles' count happened to change, which reads
as "my resize got reverted for no reason" rather than the intended
"tuicc keeps your list boxes sized to fit." A box that DOES want to
track content forever (never F2-resized) just keeps working — nothing
about launching, switching presets (F4), or content changing on its
own ever flips the flag off; only an explicit height resize does.
"""

from dataclasses import dataclass, field


@dataclass
class ModuleBox:
    name: str
    x: float   # ratio 0..1, scales with terminal width
    y: float   # ratio 0..1, scales with terminal height
    w: float | None = None   # ratio 0..1, scales with terminal width — exactly one of w/fw is set
    h: float | None = None   # ratio 0..1, scales with terminal height — exactly one of h/fh is set
    fw: int | None = None    # fixed columns (absolute terminal cells) — see module docstring
    fh: int | None = None    # fixed rows (absolute terminal cells) — see module docstring
    fh_auto: bool = False    # fh gets recomputed from the module's own content — see module docstring
    clickable: bool = True


@dataclass
class Layout:
    boxes: list[ModuleBox] = field(default_factory=list)


def boxes_to_toml_data(boxes: list[ModuleBox]) -> dict:
    """The exact inverse of config.py's build_layout_from_preset()
    parsing loop — turns ModuleBox objects back into the {"box": [...]}
    shape a preset TOML file uses, so the result round-trips through
    that same parsing loop unchanged. Used by resize mode's save.
    Writes back whichever of w/fw and h/fh the box actually uses (each
    axis independently) — a box saved while in fh/fw mode stays in that
    mode, never silently converted to a ratio (or vice versa). fh_auto
    is only written when true (a plain fh box's TOML entry stays exactly
    as clean as before this existed) — saving a box that's still in
    auto mode preserves that, saving one resize_step() already froze
    just writes its now-plain fh number, same as any other fh box.
    """
    data = []
    for box in boxes:
        entry = {"name": box.name, "x": box.x, "y": box.y}
        if box.fw is not None:
            entry["fw"] = box.fw
        else:
            entry["w"] = box.w
        if box.fh is not None:
            entry["fh"] = box.fh
            if box.fh_auto:
                entry["fh_auto"] = True
        else:
            entry["h"] = box.h
        data.append(entry)
    return {"box": data}
