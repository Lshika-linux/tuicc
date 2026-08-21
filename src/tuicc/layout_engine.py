"""Layout engine: converts a Layout's ratios into absolute terminal cells.


Layout [ratios, 0..1] + terminal size  ─>  compute_boxes() ─> {name: (x, y, w, h) in cells}


    preset (built-in) ──┐
                         ├─> Layout  ─┐
    user config delta ──┘             │
                                      v
                         layout_engine + terminal size [defined here]
                                      │
                                      v
                            absolute boxes (cells)
                                      │
                                      v
                             modules draw themselves

Takes a Layout (plain x/y/w/h ratios) plus the current terminal size,
and returns the exact row/column box each module should draw into.
Every box is independent — see layout.py's docstring for why there's
no dependency resolution here: what you configure is exactly what
renders, always.

Both axes get the SAME three deliberate exceptions to that rule, for
the same reasons on each — height (h/fh) got them first, width (w/fw)
mirrors them exactly once the same real need showed up there too (bars
wants a fixed column count, flexible height). Rather than writing each
exception out twice (once per axis, doubling this file for zero real
difference), every pass below is a small generic helper parametrized by
which axis it's resolving — pos_i/size_i pick x/w (0/2) or y/h (1/3)
for the axis actually being resolved; cross_pos_i/cross_size_i pick the
OTHER axis, used only to decide whether two boxes share enough space on
it to matter (two boxes stacked vertically only interact if they also
share columns; two lined up horizontally only interact if they also
share rows). A rect is always the same plain (x, y, w, h) cell tuple
throughout.

Exception 1 — fixed (fh or fw) boxes never shrink (that's the whole
point — see layout.py's own docstring on why some modules need an
exact row/column count to stay usable) and must never get silently
clipped by the terminal's edge either. If its own ratio-computed
position would push it past the terminal's far edge on that axis, this
holds the box's full fixed size and slides it back just enough to keep
the whole box on screen instead — the box's OWN edge always wins over
its ratio position on a terminal small enough to force the choice. This
is still fully independent per box (no coordination with neighbors) —
it just means a fixed box's position in the returned rect can differ
from its plain round(ratio * term_size) on a small enough terminal,
same "what's configured is what renders, except this one deliberate
exception" spirit as the min/max clamps resize_mode.py's own
resize_step already applies while editing. Computed directly in the
single-box "natural" pass below, no helper needed (each box only ever
reacts to the terminal's own edge here, nothing else).

Exception 2 — for flexible (h/w, not fh/fw) boxes: sidebar/preview
(height) and bars (width) were each designed to sit next to fixed-size
neighbors, but a fixed box never shrinks and a flexible one's own ratio
position has no idea a neighbor even exists — squeeze the terminal
enough and a flexible box's natural rect WILL start intruding into a
neighbor's, overlapping it rather than yielding. _squeeze_one_axis()
below does the actual work; run once per axis the box uses (h and/or w
independently), reacting ONLY to FIXED (fh for height, fw for width)
neighbors — never to another flexible box, even one on the same axis.
This is deliberately narrower than it could be (two flexible boxes
COULD in principle need to yield to each other too) — found live, width
generalizing this pass surfaced a case height alone never hit: two FH
boxes sharing a column (control/power_menu) that, once their own
row-axis conflict degrades honestly (exception 3 giving up because the
whole stack genuinely can't fit — see that exception's own docstring),
ALSO share real row-overlap as a side effect. Since both still carry a
plain, non-fw width, they were ALSO evaluated against each other by
width-squeeze on THAT (unrelated) axis — and because they share almost
the same column, one of them lost the tie and had its width zeroed
entirely: an already-accepted, honest row-axis degradation was, on top
of that, causing an unrelated and much worse column-axis one. Fixed boxes
already have their OWN dedicated coordination mechanism (exception 3) —
letting exception 2 also react to them mutually was solving the same
problem twice, badly, on the wrong axis. Restricting exception 2 to
"flexible reacts to fixed, fixed never reacts to a flexible box, and
flexible boxes never react to EACH OTHER" removes this class of bug
entirely, and costs nothing today either: no two flexible boxes on the
same axis have ever actually shared space in the packaged preset, on
either axis, so this restriction has never actually excluded a real,
needed interaction — only ones that were never being exercised anyway.
A box whose position on the OTHER axis is above/left of ours can only
intrude from our own low side (bounds how far our own low edge gets
pushed); one at/below-or-right — including an exact tie, same starting
position — can only intrude from our high side (a fixed neighbor tied
with us on our own axis is exactly "occupies our whole space", and
since only WE can ever yield here, that has to resolve to 0, not be
treated as ambiguous). A box with no meaningful overlap on the cross
axis leaves both bounds untouched — the max/min are self-correcting, a
non-intruding candidate simply loses to the box's own original edge.
Both bounds can bite the same box at once (sandwiched on both sides);
if the high bound ends up before the low bound, there's genuinely no
room left and the box gets 0 size on that axis rather than negative or
clipped — render.py's draw_all()/collect_nav_items() both skip a module
whose box has collapsed to 0 width or height, so it simply doesn't draw
(and isn't navigable to) that frame, exactly like it was never placed.
Since a fixed neighbor's own position is already fully settled by
exception 1/3 before exception 2 ever runs, there's no ordering concern
here — every fixed neighbor a flexible box reacts to is already at its
final position, not a transient one.

The overlap check on the CROSS axis needs a 1-cell TOLERANCE, not a
bare "> 0" test — found live, on Rafi's own real preset: preview's
right ratio edge (0.921875) and bars' left ratio edge (0.920833) are
hand-tuned to within a thousandth of each other (meant to sit flush,
side by side, each independently nudged via F2/F3 at some point), so
their cell positions round SEPARATELY for each box. That independent
rounding routinely lands them exactly 1 cell apart at some terminal
widths (genuinely adjacent, 0 real overlap) and exactly 1 cell
OVERLAPPING at others, flipping unpredictably as the terminal resizes —
confirmed live, sweeping term_width alone with term_height fixed
produced this flip roughly every 2-3 columns. Both boxes shared the
same natural y too, so a spurious 1-cell overlap wasn't just nudging
one edge — it was reclassified as a full mutual "we start on the same
row" intrusion each time, collapsing BOTH to 0 height in the same
frame: exactly an "immediately disappears" bug Rafi caught live, not
the gradual squeeze this pass is supposed to produce. A genuine 2+ cell
overlap is overwhelmingly likely to be real, intended overlap rather
than rounding noise between two independently-rounded edges — 1 cell
isn't enough to tell the difference, so it's treated the same as no
overlap at all. Same tolerance, same reasoning, reused for exception 3.

Exception 3 — for two FIXED (both fh, or both fw) boxes stacked/lined
up directly against each other on their shared resolving axis (control
above power_menu in the packaged preset is the fh case; no fw sibling
pair exists yet, but the mechanism is general): each one's edge-hold
(exception 1) only ever knows about the terminal's own edge, not about
a SIBLING fixed box also holding that same edge — found live, control
drawing straight into power_menu the instant the terminal got short
enough for either one's independent edge-hold to matter, since neither
has any idea the other exists. Neither can shrink (that's the whole
point of fh/fw), so the only available fix is positional:
_chain_fixed_boxes() below sorts the fixed boxes on one axis by
position descending (the one closest to the terminal's far edge first
— already correctly anchored by exception 1) and walks that order, each
box clamping its own position against the nearest ALREADY-RESOLVED
(i.e. already earlier in this same walk) box it shares meaningful space
with on the CROSS axis. Only ever moves a box's position toward the
near edge, exactly like exception 1's own clamp; if the whole stack
genuinely doesn't fit in the terminal, the box(es) nearest the near edge
clamp at 0 and may still overlap — same accepted "still can't fit"
degradation exception 1 already documents, not something this pass
tries to solve further. Run once per axis (h/fh and w/fw resolved
completely independently — a box that's fh on one axis and fw on the
other participates in both chains separately) — always AFTER the
natural pass and BEFORE exception 2's squeeze, so a flexible neighbor
reacts to a fixed box's FINAL, chained position, not its original,
possibly-overlapping one.
"""

from tuicc.layout import Layout

# Overlaps at or below this many cells, on the CROSS axis, are treated
# as rounding noise between independently-rounded box edges, not a real
# design overlap — see this module's own docstring for why (and the
# live-reproduced preview/bars flicker this threshold fixes).
MIN_MEANINGFUL_OVERLAP_CELLS = 1


def _natural_rect(box, term_width: int, term_height: int) -> tuple[int, int, int, int]:
    """Each box's own independent rect — no reaction to any other box
    yet, just this one box's ratios (or fixed size + edge-hold) against
    the terminal. See this module's own docstring, exception 1.
    """
    if box.fw is not None:
        w = box.fw
        x = round(box.x * term_width)
        if x + w > term_width:
            x = max(term_width - w, 0)
    else:
        x = round(box.x * term_width)
        w = round(box.w * term_width)

    if box.fh is not None:
        h = box.fh
        y = round(box.y * term_height)
        if y + h > term_height:
            y = max(term_height - h, 0)
    else:
        y = round(box.y * term_height)
        h = round(box.h * term_height)

    return (x, y, w, h)


def _chain_fixed_boxes(layout: Layout, resolved: dict, *, fixed_attr: str,
                        pos_i: int, size_i: int, cross_pos_i: int, cross_size_i: int) -> None:
    """Exception 3 — mutates `resolved` in place. See this module's own
    docstring for the full reasoning; fixed_attr is "fh" or "fw" (which
    boxes this chain applies to), pos_i/size_i is the axis being
    resolved (1/3 for y/h, 0/2 for x/w), cross_pos_i/cross_size_i is
    the other axis (used only for the overlap filter).
    """
    ordered = sorted(
        (b for b in layout.boxes if getattr(b, fixed_attr) is not None),
        key=lambda b: resolved[b.name][pos_i],
        reverse=True,
    )
    for box in ordered:
        rect = list(resolved[box.name])
        pos, size = rect[pos_i], rect[size_i]
        wall = None
        for other in ordered:
            if other is box:
                continue
            orect = resolved[other.name]
            opos = orect[pos_i]
            if opos < pos:
                continue  # not yet resolved — still further from the edge than us
            cross_overlap = (
                min(rect[cross_pos_i] + rect[cross_size_i], orect[cross_pos_i] + orect[cross_size_i])
                - max(rect[cross_pos_i], orect[cross_pos_i])
            )
            if cross_overlap <= MIN_MEANINGFUL_OVERLAP_CELLS:
                continue
            if wall is None or opos < wall:
                wall = opos
        if wall is not None and pos + size > wall:
            pos = max(wall - size, 0)
        rect[pos_i] = pos
        resolved[box.name] = tuple(rect)


def _squeeze_one_axis(rect: list, other_rects: list, *,
                       pos_i: int, size_i: int, cross_pos_i: int, cross_size_i: int) -> list:
    """Exception 2, for a single box on a single axis — returns a new
    rect (list, so the caller can chain h-then-w on the same box
    without mutating anyone else's). other_rects is always just the
    FIXED (fh/fw, as appropriate) neighbors — see this module's own
    docstring for why flexible-vs-flexible reactions were deliberately
    dropped, and why that means every rect in other_rects is already
    fully resolved (no ordering/staleness concerns here).
    """
    pos, size = rect[pos_i], rect[size_i]
    low_bound, high_bound = pos, pos + size
    for orect in other_rects:
        cross_overlap = (
            min(rect[cross_pos_i] + rect[cross_size_i], orect[cross_pos_i] + orect[cross_size_i])
            - max(rect[cross_pos_i], orect[cross_pos_i])
        )
        if cross_overlap <= MIN_MEANINGFUL_OVERLAP_CELLS:
            continue  # no meaningful overlap on the cross axis — can't intrude
        opos = orect[pos_i]
        if opos < pos:
            low_bound = max(low_bound, opos + orect[size_i])
        else:
            high_bound = min(high_bound, opos)
    pos = max(pos, low_bound)
    size = max(high_bound - pos, 0)
    new_rect = list(rect)
    new_rect[pos_i] = pos
    new_rect[size_i] = size
    return new_rect


def compute_boxes(layout: Layout, term_width: int, term_height: int) -> dict[str, tuple[int, int, int, int]]:
    natural = {box.name: _natural_rect(box, term_width, term_height) for box in layout.boxes}

    resolved = dict(natural)
    _chain_fixed_boxes(layout, resolved, fixed_attr="fh", pos_i=1, size_i=3, cross_pos_i=0, cross_size_i=2)
    _chain_fixed_boxes(layout, resolved, fixed_attr="fw", pos_i=0, size_i=2, cross_pos_i=1, cross_size_i=3)

    result = {}
    for box in layout.boxes:
        rect = list(resolved[box.name])
        if box.h is not None:
            fixed_neighbors = [resolved[o.name] for o in layout.boxes if o is not box and o.fh is not None]
            rect = _squeeze_one_axis(rect, fixed_neighbors, pos_i=1, size_i=3, cross_pos_i=0, cross_size_i=2)
        if box.w is not None:
            fixed_neighbors = [resolved[o.name] for o in layout.boxes if o is not box and o.fw is not None]
            rect = _squeeze_one_axis(rect, fixed_neighbors, pos_i=0, size_i=2, cross_pos_i=1, cross_size_i=3)
        result[box.name] = tuple(rect)
    return result
