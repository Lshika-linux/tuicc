"""Layout engine: converts a Layout's ratios into absolute terminal cells.


Layout [ratios, 0..1] + terminal size  ─>  compute_boxes() ─> {name: (x, y, w, h) in cells}


    preset (built-in) ──┐
                         ├─> Layout  ─┐
    user config delta ──┘             │
                                      v
                         layout_engine + terminal size [defined here]
                                      │
                                      v
                            absolute boxes (cells) [defined here]
                                      │
                                      v
                             modules draw themselves

Takes a Layout (ratios, 0..1, fixed counts, or box references) plus the
current terminal size, and returns the exact row/column box each module
should draw into. Knows nothing about where the Layout or the terminal
size came from — just does the math.

DEPENDENCY RESOLUTION: a box referencing another box (right_of, below,
above, fill_to) can't be computed until that other box is fully resolved
(x, y, w, h all known). This resolves in passes — compute whatever's
ready, repeat — until either everything's resolved or a pass makes no
progress (a missing reference or a dependency cycle), which is a hard
error, not a silent partial layout.

PER-BOX FIELD ORDER: within a single box, most fields are independent
of each other, but two combinations aren't:
  - 'above'/'bottom' (y-group) need this box's OWN height already known,
    since y is computed FROM the height (term_height - h, or ref.y - h).
  - 'fill_to' (h-group) needs this box's OWN y already known, since
    height is computed as (ref.y - this box's y).
So the order per box is: x, w, then (y if it doesn't need h) or (h if it
needs y first) ... see the code below for the exact branching. Combining
fill_to with above/bottom on the SAME box would be circular (h needs y,
y needs h) and isn't supported — see layout.py's docstring for why the
'below' chain and the 'above'/'bottom'/'fill_to' chain are meant to be
used on opposite ends of a stack, not mixed within one box.
"""

from tuicc.layout import Layout


def compute_boxes(layout: Layout, term_width: int, term_height: int) -> dict[str, tuple[int, int, int, int]]:
    resolved = {}
    pending = list(layout.boxes)

    while pending:
        still_pending = []
        progressed = False

        for box in pending:
            deps = [d for d in (box.right_of, box.below, box.above, box.fill_to) if d is not None]
            if any(d not in resolved for d in deps):
                still_pending.append(box)
                continue

            # x
            if box.right_of is not None:
                ref_x, _, ref_w, _ = resolved[box.right_of]
                x = ref_x + ref_w
            else:
                x = round(box.x * term_width)

            # w
            w = box.cols if box.cols is not None else round(box.w * term_width)

            y_needs_own_height = box.above is not None or box.bottom

            # y, if it doesn't need this box's own height first
            if not y_needs_own_height:
                if box.below is not None:
                    _, ref_y, _, ref_h = resolved[box.below]
                    y = ref_y + ref_h
                else:
                    y = round(box.y * term_height)

            # h
            if box.fill_to is not None:
                _, ref_y, _, _ = resolved[box.fill_to]
                h = ref_y - y
            else:
                h = box.rows if box.rows is not None else round(box.h * term_height)

            # y, if it needed this box's own height (now known)
            if y_needs_own_height:
                if box.bottom:
                    y = term_height - h
                else:  # box.above is not None
                    _, ref_y, _, _ = resolved[box.above]
                    y = ref_y - h

            resolved[box.name] = (x, y, w, h)
            progressed = True

        if not progressed:
            unresolved = [b.name for b in still_pending]
            raise KeyError(
                f"Could not resolve layout for box(es) {unresolved}: "
                f"'right_of'/'below'/'above'/'fill_to' references a box "
                f"that doesn't exist, or the boxes form a dependency cycle."
            )

        pending = still_pending

    return resolved
