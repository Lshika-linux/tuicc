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
    # Process id owning this window, when the provider's WM exposes one —
    # sway's IPC tree includes it directly; i3's does not (confirmed
    # against both providers' IPC docs). None on any provider that can't
    # supply it, same optionality as mark_self() for providers without an
    # equivalent concept — code depending on pid must handle None.
    pid: int | None = None
    # GitHub issue #8 (tabbed/stacked layout): a sway/i3 "stacked" or
    # "tabbed" container's children all share the exact same rect —
    # only one is actually shown at a time, the rest are hidden behind
    # it. tab_group_id (the container's own con id, stringified) is
    # shared by every window in the same such container; None means
    # "not part of one" (an ordinary tiled/floating window, still the
    # overwhelming majority case). tab_group_layout is "stacked" or
    # "tabbed" (mirrors the container's own con.layout verbatim) — the
    # two have genuinely different real on-screen conventions (a
    # tabbed container shows one horizontal strip of titles; a stacked
    # one shows one full-width title row per window), so callers need
    # to know which, not just "grouped". tab_active is whether THIS
    # window is the one actually visible right now within its own
    # group (meaningless — always False — outside a group). See
    # providers/sway.py's parse_tree() for how these are populated:
    # walking con.layout/con.focus directly, information workspace.
    # leaves() alone (the old, still layout-blind traversal) discards
    # entirely.
    tab_group_id: str | None = None
    tab_group_layout: str | None = None
    tab_active: bool = False

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
