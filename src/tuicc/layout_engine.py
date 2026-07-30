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

Takes a Layout (ratios, 0..1) plus the current terminal size, and returns
the exact row/column box each module should draw into. Knows nothing about
where the Layout or the terminal size came from — just does the math.
"""

from tuicc.layout import Layout


def compute_boxes(layout: Layout, term_width: int, term_height: int) -> dict[str, tuple[int, int, int, int]]:
    boxes = {}

    for box in layout.boxes:
        x = round(box.x * term_width)
        y = round(box.y * term_height)
        w = round(box.w * term_width)
        h = box.rows if box.rows is not None else round(box.h * term_height)

        boxes[box.name] = (x, y, w, h)

    return boxes
