"""
Layout model: where each module sits on screen, as ratios of the whole terminal.

    preset (built-in) ──┐
                         ├─> Layout [ratios, defined here]   ─┐
    user config delta ──┘                                     │
                                                              v
                                                layout_engine + terminal size
                                                              │
                                                              v
                                                     absolute boxes (cells)
                                                              │
                                                              v
                                                     modules draw themselves

This file only defines the *shape* of a layout (ratios, 0..1). Combining
presets with user overrides, and converting ratios into actual terminal
rows/columns, happens in layout_engine.py
"""

from dataclasses import dataclass, field


@dataclass
class ModuleBox:
    name: str
    x: float
    y: float
    w: float
    # Exactly one of h/rows is set — enforced at load time (config.py), not here.
    h: float | None = None       # ratio 0..1, scales with terminal height
    rows: int | None = None      # absolute row count, does not scale
    clickable: bool = True


@dataclass
class Layout:
    boxes: list[ModuleBox] = field(default_factory=list)
