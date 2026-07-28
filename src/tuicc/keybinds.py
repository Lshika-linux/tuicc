"""Keybinding resolution: maps config key names to curses key codes.

Special names (arrows, Tab, Enter...) map to their curses constants.
A single character maps via ord() — so users can bind e.g. "h"/"j"/
"k"/"l" instead of arrow keys, and uppercase letters work as an
implicit Shift (curses reports "A" and "a" as different codes on
their own, no special handling needed).

Shift+Tab is the one Shift combination handled explicitly, via
curses.KEY_BTAB — a standard, reliably supported terminal code.
General "any key + Shift" isn't attempted: curses doesn't expose
modifier state separately from the key itself for most keys, and
support for e.g. Shift+arrow varies by terminal.

Kept pure (no live curses screen needed to resolve a constant), same
reasoning as theme.py vs theme_setup.py.
"""

import curses


SPECIAL_KEYS = {
    "Left": curses.KEY_LEFT,
    "Right": curses.KEY_RIGHT,
    "Up": curses.KEY_UP,
    "Down": curses.KEY_DOWN,
    "Tab": ord("\t"),
    "Shift+Tab": curses.KEY_BTAB,
    "Enter": 10,
    "Escape": 27,
    "Space": ord(" "),
}


def resolve_key(name: str) -> int:
    if name in SPECIAL_KEYS:
        return SPECIAL_KEYS[name]

    if len(name) == 1:
        return ord(name)

    raise ValueError(
        f"Unknown key name: {name!r}. Expected one of "
        f"{list(SPECIAL_KEYS.keys())}, or a single character."
    )
