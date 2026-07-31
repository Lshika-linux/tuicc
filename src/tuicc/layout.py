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

This file only defines the *shape* of a layout (ratios, 0..1, or fixed
counts, or references to other boxes). Combining presets with user
overrides, and converting all of that into actual terminal rows/columns,
happens in layout_engine.py.

--- Positioning fields, in detail ---

x-group (exactly one): x (manual ratio) OR right_of (name of another box —
x becomes that box's actual right edge).

y-group (exactly one): y (manual ratio) OR below (name of another box — y
becomes that box's actual bottom edge) OR above (name of another box — y
is set so THIS box's bottom edge lands exactly on that box's top edge —
i.e. this box sits directly above it) OR bottom (True — y is computed as
term_height - this box's own height, i.e. flush against the screen's
bottom edge, independent of any other box).

w-group (exactly one): w (manual ratio) OR cols (fixed column count).

h-group (exactly one): h (manual ratio) OR rows (fixed row count) OR
fill_to (name of another box — height becomes exactly enough to reach
that box's top edge from this box's own y, i.e. "fill whatever's left").

Why 'above'/'bottom' exist as well as 'below', and why 'fill_to' exists:
a box stack that's anchored from the top (sidebar y=0, connectivity
below=sidebar, power_menu below=connectivity) works fine ONLY if every
box in the stack has a fixed or otherwise-exactly-known height. The
moment one box (like sidebar) is ratio-sized while its neighbors are
fixed-row, no single ratio can make the stack's total exactly equal
term_height across different terminal heights — the fixed portion is
additive (constant rows), the ratio portion is multiplicative (percent),
and they don't scale together. The fix is to anchor the fixed-size boxes
from the BOTTOM instead (power_menu: bottom=True, connectivity:
above="power_menu"), and let the ratio/flexible box (sidebar) use
fill_to to consume exactly what's left above them. This makes the column
sum to term_height exactly, for any terminal height, with no manual
per-height tuning — see layout_engine.py's compute_boxes() for the actual
math and the dependency-resolution order this requires.
"""

from dataclasses import dataclass, field


@dataclass
class ModuleBox:
    name: str
    # Exactly one of x/right_of is set — enforced at load time (config.py), not here.
    x: float | None = None       # ratio 0..1, a manual position
    right_of: str | None = None  # name of another box — x becomes that box's
                                  # actual right edge, whatever it resolves to
    # Exactly one of y/below/above/bottom is set — enforced at load time (config.py).
    y: float | None = None       # ratio 0..1, a manual position
    below: str | None = None     # name of another box — y becomes that box's
                                  # actual bottom edge, whatever it resolves to
    above: str | None = None     # name of another box — this box's bottom
                                  # edge lands exactly on that box's top edge
    bottom: bool = False         # y = term_height - this box's own height
    # Exactly one of w/cols is set — enforced at load time (config.py), not here.
    w: float | None = None       # ratio 0..1, scales with terminal width
    cols: int | None = None      # absolute column count, does not scale
    # Exactly one of h/rows/fill_to is set — enforced at load time (config.py).
    h: float | None = None       # ratio 0..1, scales with terminal height
    rows: int | None = None      # absolute row count, does not scale
    fill_to: str | None = None   # name of another box — height reaches
                                  # exactly to that box's top edge
    clickable: bool = True


@dataclass
class Layout:
    boxes: list[ModuleBox] = field(default_factory=list)
