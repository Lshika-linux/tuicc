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
        rx, ry, rw, rh = box.rect

        x = round(rx * term_width)
        y = round(ry * term_height)
        w = round(rw * term_width)
        h = round(rh * term_height)

        boxes[box.name] = (x, y, w, h)

    return boxes
