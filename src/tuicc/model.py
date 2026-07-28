"""Shared data model that all WM providers translate into.

    WM data -> selected provider -> tuicc data [defined here] -> modules

This is the "generic language" the rest of tuicc speaks. Providers
(sway, i3, scroll, ...) are the only code allowed to know about
WM-specific details — everything else only ever sees these types.
"""

from dataclasses import dataclass, field


@dataclass
class Window:
    id: str
    app_id: str
    title: str
    focused: bool
    rect: tuple[float, float, float, float]  # x, y, w, h — normalized 0..1 within the region
    floating: bool = False

@dataclass
class Region:
    id: str
    name: str
    windows: list[Window] = field(default_factory=list)
    focused: bool = False
    active: bool = True


@dataclass
class WMState:
    regions: list[Region] = field(default_factory=list)
    focused_region_id: str | None = None
