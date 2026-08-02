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
"""

from tuicc.layout import Layout


def compute_boxes(layout: Layout, term_width: int, term_height: int) -> dict[str, tuple[int, int, int, int]]:
    return {
        box.name: (
            round(box.x * term_width),
            round(box.y * term_height),
            round(box.w * term_width),
            round(box.h * term_height),
        )
        for box in layout.boxes
    }
