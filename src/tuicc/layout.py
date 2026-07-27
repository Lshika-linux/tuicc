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

This file only defines the *shape* of a layout (ratios, 0..1). Combining
presets with user overrides, and converting ratios into actual terminal
rows/columns, happens in layout_engine.py
"""

from dataclasses import dataclass, field


@dataclass
class ModuleBox:
    name: str
    rect: tuple[float, float, float, float]  # x, y, w, h — normalized 0..1, relative to the whole screen
    clickable: bool = True


@dataclass
class Layout:
    boxes: list[ModuleBox] = field(default_factory=list)
