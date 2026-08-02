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

Every box is a plain, independent x/y/w/h ratio (0.0-1.0 of the
terminal's width/height) — nothing here coordinates with anything
else. Resizing or repositioning one box never moves or resizes
another; what you configure is exactly what renders. If a box's ratio
looks wrong on a very different terminal size than the one you set it
up on, that's expected — interactive resize mode (main.py) is the
fix-it-when-you-see-it mechanism, not an automatic guarantee.
"""

from dataclasses import dataclass, field


@dataclass
class ModuleBox:
    name: str
    x: float   # ratio 0..1, scales with terminal width
    y: float   # ratio 0..1, scales with terminal height
    w: float   # ratio 0..1, scales with terminal width
    h: float   # ratio 0..1, scales with terminal height
    clickable: bool = True


@dataclass
class Layout:
    boxes: list[ModuleBox] = field(default_factory=list)


def boxes_to_toml_data(boxes: list[ModuleBox]) -> dict:
    """The exact inverse of config.py's build_layout_from_preset()
    parsing loop — turns ModuleBox objects back into the {"box": [...]}
    shape a preset TOML file uses, so the result round-trips through
    that same parsing loop unchanged. Used by resize mode's save.
    """
    return {"box": [
        {"name": box.name, "x": box.x, "y": box.y, "w": box.w, "h": box.h}
        for box in boxes
    ]}
